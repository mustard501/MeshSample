///////////////////////////////////////////////////////////////////////////////
//         Mesh2Splat: fast mesh to 3D gaussian splat conversion             //
//        Copyright (c) 2025 Electronic Arts Inc. All rights reserved.       //
///////////////////////////////////////////////////////////////////////////////

#pragma once
#include <string>
#include <vector>
#include <optional> 
#include <filesystem>
#include <imgui.h>
#include <imgui_impl_glfw.h>
#include <imgui_impl_opengl3.h>
#include <glm/glm.hpp>
#include "utils/utils.hpp"
#include "ImGuiFileDialog.h"



class ImGuiUI {
public:
    ImGuiUI(float defaultGaussianStd, float defaultMesh2SPlatQuality);
    ~ImGuiUI();

    void preframe();
    void initialize(GLFWwindow* window);
    void renderUI();
    void displayGaussianCounts(unsigned int gaussianCount, unsigned int visibleGaussianCount);
    void postframe();

    bool shouldRunConversion() const;
    bool shouldLoadNewMesh() const;
    //Batch processing stuff
    bool hasBatchWork() const;                        // any Queued left?
    bool isBatchRunning() const;                      // UI is in "running" state

                          // flag cancel; new items not dispatched

    bool shouldBatchLoadNewMeshes() const ;
    bool shouldSavePly() const;
    bool wasMeshLoaded() const;
    bool shouldLoadPly() const;
    bool wasPlyLoaded() const;

    std::string getMeshFilePath() const;
    std::string getMeshFilePathParentFolder() const;
    std::string getMeshFullFilePathDestination() const;
    std::string getPlyFilePathParentFolder() const;
    std::string getPlyFilePath() const;


    float getGaussianStd() const;
    int getResolutionTarget() const;
    unsigned int getFormatOption() const;

    glm::vec4 getSceneBackgroundColor() const;

    void setLoadNewMesh(bool shouldLoadNewMesh);
    void setBatchLoadNewMeshes(bool shouldBatchLoadNewMeshes);
    void setMeshLoaded(bool loaded);
    void setRunConversion(bool shouldRunConversionFlag);
    void setShouldSavePly(bool shouldSavePly);
    void setFrameMetrics(double gpuFrameTime);
    void setLoadNewPly(bool loadedPly);
    void setPlyLoaded(bool loadedPly);

    void renderFileSelectorWindow();
    void renderPropertiesWindow();
    void renderGpuFrametime();

    //Batch functions
    struct BatchItem {
        std::string path;
        std::string parent;
        utils::ModelFileExtension ext;
        enum class Status { Queued, Processing, Done, Failed } status = Status::Queued;
        std::string outPath; // filled by UI using your current output naming rules
        std::string error;   // filled if failed
    };

    BatchItem* popNextBatchItem();      // get next Queued -> set to Processing
    void markBatchItemDone(const std::string& path);  // Processing -> Done
    void markBatchItemFailed(const std::string& path, const std::string& err);
    void cancelBatch();     
    const std::vector<ImGuiUI::BatchItem>& ImGuiUI::getBatchItems() const;

private:
    int resolutionIndex = 0;
    const int resolutionOptions[3] = { 1024, 2048, 4096 };
    const char* resolutionLabels[3] = { "1024", "2048", "4096" };
    
    int formatIndex = 0;
    const unsigned int formatOptions[4] = { 0, 1, 2, 3 };
    const char* formatLabels[4] = {
        "PLY Standard Format",
        "PLY PBR",
        "PLY Compressed PBR",
        "PLY BRDF (base sRGB, N, MR)"
    };

    float gaussian_std;
    bool runConversionFlag = false;
    bool loadNewMesh = false;
    bool batchLoadNewMeshes = false; 
    bool loadNewPly = false;

    bool hasPlyBeenLoaded = false;
    bool hasMeshBeenLoaded = false;

    bool savePly = false;

    std::string meshFilePath;
    std::string meshParentFolder;

    std::string plyFilePath;
    std::string plyParentFolder;

    utils::ModelFileExtension currentModelFormat = utils::ModelFileExtension::NONE;

    std::string destinationFilePathFolder = "";
    char outputFilename[256] = "DefaultFilename.ply";


    const float minStd = 0.1f;
    const float maxStd = 2.0f;
    int maxRes = 1024; //TBH not sure what best value is here

    std::vector<int> discreteResolutionOptions;
    std::vector<std::string> discreteResolutionLabelStorage;
    std::vector<const char*> discreteResolutionComboItems;
    int discreteResolutionIndex = 0;

    //Gpu timing data
    std::vector<float> frameTimeHistory = {0.0f};
    static constexpr size_t MAX_FRAME_HISTORY = 100;
    double gpuFrameTime = 0;
    float maxPlotTimeMs = 100.0f; 
    float targetFrameTimeThreshold = 16.6f; 

    glm::vec4 sceneBackgroundColor = { 0,0,0,1 };

    //Batching data
    std::vector<BatchItem> batchItems;
    bool batchIncludeSubfolders = false;
    bool batchRunning = false;
    bool batchCancelRequested = false;
    float batchProgress01 = 0.0f;
    int batchSelectedRow = -1;

    void renderBatchWindow();                  // NEW panel
    void enqueueFolder(const std::string& dir);

    /// Rebuild powers-of-two resolutions in (16, maxRes]; pick entry closest to preferredR.
    void refreshDiscreteResolutionOptions(int preferredR);
    
    static bool isSupportedMesh(const std::filesystem::directory_entry& e)
    {
        if (!e.is_regular_file()) return false;
        auto ext = e.path().extension().string();
        std::transform(ext.begin(), ext.end(), ext.begin(), ::tolower);
        return (ext == ".glb" || ext == ".ply");
    };

    static utils::ModelFileExtension extFromPath(const std::string& p)
    {
        auto ext = utils::getFileExtension(p);
        if (ext == utils::ModelFileExtension::GLB) return utils::ModelFileExtension::GLB;
        if (ext == utils::ModelFileExtension::PLY) return utils::ModelFileExtension::PLY;
        return utils::ModelFileExtension::NONE;
    };

};
