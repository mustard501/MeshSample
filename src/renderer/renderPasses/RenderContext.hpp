///////////////////////////////////////////////////////////////////////////////
//         Mesh2Splat: fast mesh to 3D gaussian splat conversion             //
//        Copyright (c) 2025 Electronic Arts Inc. All rights reserved.       //
///////////////////////////////////////////////////////////////////////////////

#pragma once

#include "utils/utils.hpp"
#include "utils/glUtils.hpp"
#include "utils/ShaderRegistry.hpp"

enum PassesDebugIDs
{
    CONVERSION_PASS                     = 0,
};

//TODO: split up in sub contexts
struct RenderContext {
    // Parameters
    std::string meshFilePath;
    std::string baseFolder;
    bool isWindowMinimized;
    int resolution;
    glm::ivec2 rendererResolution;
    float gaussianStd = 0.65f;
    glm::mat4 modelMat = glm::mat4(1);
    glm::mat4 viewMat = glm::mat4(1);
    glm::mat4 projMat = glm::mat4(1);
    glm::mat4 MVP; //TODO: yeah, assumes we will render with one single model mat

    glm::vec3 hfov_focal;
    glm::vec3 camPos;
    float nearPlane;
    float farPlane;
    GLFWwindow* rendererGlfwWindow; //TODO: I also need to store this here for now, as I need to reset the viewport DURING the rendering pass as it may ha

    ShaderRegistry shaderRegistry;

    int normalizedUvSpaceWidth;
    int normalizedUvSpaceHeight;
    unsigned int resolutionTarget; 
    unsigned int format; //0: from mesh2splat, 1: classic .ply 3dgs, 2: compressedPBR
    bool plyHasPbr = false; // if format == 1 (loaded ply file), does ply support pbr rendering

    // Resources
    GLuint vao;
    GLuint gaussianBuffer;
    /** Per-gaussian records from conversion (pixel + triangle id); see lod/non-merge/GaussianPixelTable.hpp */
    GLuint conversionGaussianMetaBuffer;
    bool conversionPixelTableValid = false;
    GLuint atomicCounterBufferConversionPass;
    GLint numberOfGaussians;

    // Data Structures
    std::vector<std::pair<utils::Mesh, utils::GLMesh>> dataMeshAndGlMesh;
    std::map<std::string, utils::TextureDataGl> textureTypeMap;
    float totalSurfaceArea = 0;

    std::map<std::string, std::map<std::string, utils::TextureDataGl>> meshToTextureData;

    utils::MaterialGltf material;
    std::vector<utils::GaussianDataSSBO> readGaussians;
    
    std::deque<GLuint> queryPool;
};