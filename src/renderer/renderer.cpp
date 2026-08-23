///////////////////////////////////////////////////////////////////////////////
//         Mesh2Splat: fast mesh to 3D gaussian splat conversion             //
//        Copyright (c) 2025 Electronic Arts Inc. All rights reserved.       //
///////////////////////////////////////////////////////////////////////////////

#include "renderer.hpp"
#include "conversion/GaussianPixelTable.hpp"

//TODO: create a separete camera class, avoid it bloating and getting too messy

Renderer::Renderer(GLFWwindow* window) : renderContext {}
{

    rendererGlfwWindow = window;
    renderPassesOrder = {};

    sceneManager = std::make_unique<SceneManager>(renderContext);
    renderContext.gaussianBuffer                = 0;
    renderContext.conversionGaussianMetaBuffer  = 0;
    renderContext.conversionPixelTableValid     = false;
    renderContext.atomicCounterBufferConversionPass = 0;

    renderContext.normalizedUvSpaceWidth        = 0;
    renderContext.normalizedUvSpaceHeight       = 0;
    renderContext.rendererGlfwWindow            = window; //TODO: this double reference is ugly, refactor

    
    lastShaderCheckTime      = glfwGetTime();

    //TODO: should this maybe live in the Renderer rather than shader utils? Probably yes
    glUtils::initializeShaderLocations();
    
    glUtils::initializeShaderFileMonitoring(renderContext.shaderRegistry);

    updateShadersIfNeeded(true); //Forcing compilation
    
    glGenVertexArrays(1, &(renderContext.vao));
    glGenBuffers(1, &(renderContext.gaussianBuffer));
    glGenBuffers(1, &(renderContext.conversionGaussianMetaBuffer));

    glUtils::resizeAndBindToPosSSBO<glm::vec4>(
        (MAX_GAUSSIANS_TO_SORT * sizeof(utils::GaussianDataSSBO)) / sizeof(glm::vec4),
        renderContext.gaussianBuffer, 0);
    glBindBuffer(GL_SHADER_STORAGE_BUFFER, renderContext.conversionGaussianMetaBuffer);
    glBufferData(
        GL_SHADER_STORAGE_BUFFER,
        static_cast<GLsizeiptr>(MAX_GAUSSIANS_TO_SORT) * sizeof(conversion::ConversionGaussianMeta),
        nullptr,
        GL_DYNAMIC_DRAW);
    glBindBuffer(GL_SHADER_STORAGE_BUFFER, 0);

    for (size_t i = 0; i < 10; ++i) {
        GLuint query;
        glGenQueries(1, &query);
        renderContext.queryPool.push_back(query);
    }

    // Second atomic counter
    glGenBuffers(1, &renderContext.atomicCounterBufferConversionPass);
    glBindBuffer(GL_ATOMIC_COUNTER_BUFFER, renderContext.atomicCounterBufferConversionPass);
    glBufferData(GL_ATOMIC_COUNTER_BUFFER, sizeof(GLuint), nullptr, GL_DYNAMIC_DRAW);
    GLuint zeroVal = 0;
    glBufferSubData(GL_ATOMIC_COUNTER_BUFFER, 0, sizeof(GLuint), &zeroVal);
    glBindBuffer(GL_ATOMIC_COUNTER_BUFFER, 0);

}

Renderer::~Renderer()
{
    glDeleteVertexArrays(1, &(renderContext.vao));

    glDeleteBuffers(1, &(renderContext.gaussianBuffer));
    glDeleteBuffers(1, &(renderContext.conversionGaussianMetaBuffer));
    glDeleteBuffers(1, &(renderContext.atomicCounterBufferConversionPass));

    for (auto& query : renderContext.queryPool) {
        glDeleteQueries(1, &query);
    }
}

void Renderer::initialize() {

    renderPasses[conversionPassName] = std::make_unique<ConversionPass>();

    renderPassesOrder = {
        conversionPassName
    };
}

void Renderer::renderFrame()
{
    if (!renderContext.queryPool.empty()) {
        GLuint currentQuery = renderContext.queryPool.front();
        glBeginQuery(GL_TIME_ELAPSED, currentQuery);
    }

    for (auto& renderPassName : renderPassesOrder)
    {
        auto& passPtr = renderPasses[renderPassName];
        if (passPtr->isEnabled())
        {
            passPtr->execute(renderContext);
            passPtr->setIsEnabled(false); //Default to false for next frame
        }
    }

    if (!renderContext.queryPool.empty()) {
        GLuint currentQuery = renderContext.queryPool.front();
        glEndQuery(GL_TIME_ELAPSED);
    
        renderContext.queryPool.pop_front();
        renderContext.queryPool.push_back(currentQuery);
    }
    
    if (renderContext.queryPool.size() > 5) {
        GLuint completedQuery = renderContext.queryPool.front();
        GLuint64 elapsedTime = 0;
        glGetQueryObjectui64v(completedQuery, GL_QUERY_RESULT, &elapsedTime);
        this->gpuFrameTimeMs = static_cast<double>(elapsedTime) / 1e6; // ns to ms
    }
};        

void Renderer::clearingPrePass(glm::vec4 clearColor)
{
    glClearColor(clearColor.r, clearColor.g, clearColor.b, 0); //alpha==0 Important for correct blending --> but still front to back expects first DST to be (0,0,0,0)
    //TODO: find way to circumvent first write, as bkg color should not be accounted for in blending
    glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT);
}

void Renderer::setLastShaderCheckTime(double lastShaderCheckedTime)
{
    this->lastShaderCheckTime = lastShaderCheckedTime;
}

double Renderer::getLastShaderCheckTime()
{
    return lastShaderCheckTime;
}

RenderContext* Renderer::getRenderContext()
{
    return &renderContext;
}

void Renderer::enableRenderPass(std::string renderPassName)
{
    if (auto renderPass = renderPasses.find(renderPassName); renderPass != renderPasses.end())
    {
        renderPass->second->setIsEnabled(true);
    } else {
        std::cerr << "RenderPass: [ "<< renderPassName << " ] not found." << std::endl;
    }
}

void Renderer::setViewportResolutionForConversion(int resolutionTarget)
{
    renderContext.resolutionTarget = resolutionTarget;
}

            
void Renderer::setFormatType(unsigned int format)
{
    renderContext.format = format;
    renderContext.conversionPixelTableValid = false;
};

void Renderer::resetRendererViewportResolution()
{
    int width, height;
    glfwGetFramebufferSize(rendererGlfwWindow, &width, &height);
    renderContext.rendererResolution = glm::ivec2(width, height);
}

void Renderer::setStdDevFromImGui(float stdDev)
{
    renderContext.gaussianStd = stdDev;
}

SceneManager& Renderer::getSceneManager()
{
    return *sceneManager;
}

double Renderer::getTotalGpuFrameTimeMs() const { return gpuFrameTimeMs; }

bool Renderer::isWindowMinimized()
{
    return glfwGetWindowAttrib(rendererGlfwWindow, GLFW_ICONIFIED);
}


void Renderer::updateGaussianBuffer()
{
    glUtils::fillGaussianBufferSsbo(renderContext.gaussianBuffer, renderContext.readGaussians);
    int buffSize = 0;
    glBindBuffer          (GL_SHADER_STORAGE_BUFFER, renderContext.gaussianBuffer);
    glGetBufferParameteriv(GL_SHADER_STORAGE_BUFFER, GL_BUFFER_SIZE, &buffSize);
    renderContext.numberOfGaussians = buffSize / sizeof(utils::GaussianDataSSBO);
    glBindBuffer(GL_SHADER_STORAGE_BUFFER, 0);
}

void Renderer::gaussianBufferFromSize(unsigned int size)
{
    glUtils::fillGaussianBufferSsbo(renderContext.gaussianBuffer, size);
}

bool Renderer::updateShadersIfNeeded(bool forceReload) {
    return renderContext.shaderRegistry.reloadModifiedShaders(forceReload);
}

unsigned int Renderer::getVisibleGaussianCount()
{
    return this->renderContext.numberOfGaussians;

}

unsigned int Renderer::getTotalGaussianCount()
{
    return this->renderContext.numberOfGaussians;

}

