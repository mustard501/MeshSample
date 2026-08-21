///////////////////////////////////////////////////////////////////////////////
//         Mesh2Splat: fast mesh to 3D gaussian splat conversion             //
//        Copyright (c) 2025 Electronic Arts Inc. All rights reserved.       //
///////////////////////////////////////////////////////////////////////////////

#pragma once
#include "utils/utils.hpp"
#include "parsers/parsers.hpp"
#include "utils/glUtils.hpp"
#include "renderPasses/RenderContext.hpp"
#include "RenderPasses.hpp"
#include "utils/SceneManager.hpp"
#include "utils/ShaderRegistry.hpp"

class Renderer {
public:
	Renderer(GLFWwindow* window);

	~Renderer();

	void initialize();
	void renderFrame();        
	void clearingPrePass(glm::vec4 clearColor); 

	//TODO: For now not using this, will implement a render-pass based structure and change how the render-loop is implemented
	bool updateShadersIfNeeded(bool forceReload = false);
	unsigned int getVisibleGaussianCount();
	unsigned int getTotalGaussianCount();
	void setLastShaderCheckTime(double lastShaderCheckedTime);
	double getLastShaderCheckTime();
	RenderContext* getRenderContext();
	void enableRenderPass(std::string renderPassName);
	void setViewportResolutionForConversion(int resolutionTarget);
	void setFormatType(unsigned int format);

	void setStdDevFromImGui(float stdDev);
	void resetRendererViewportResolution();
	SceneManager& getSceneManager();
	double getTotalGpuFrameTimeMs() const;
	void updateGaussianBuffer();
	void gaussianBufferFromSize(unsigned int size);
	bool isWindowMinimized();

private:
	std::map<std::string, std::unique_ptr<IRenderPass>> renderPasses;
	std::vector<std::string> renderPassesOrder;

	GLFWwindow* rendererGlfwWindow;

	std::unique_ptr<SceneManager> sceneManager;
	RenderContext renderContext;

	double lastShaderCheckTime;

	double gpuFrameTimeMs;

};