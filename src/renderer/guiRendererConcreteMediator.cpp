///////////////////////////////////////////////////////////////////////////////
//         Mesh2Splat: fast mesh to 3D gaussian splat conversion             //
//        Copyright (c) 2025 Electronic Arts Inc. All rights reserved.       //
///////////////////////////////////////////////////////////////////////////////

#include "GuiRendererConcreteMediator.hpp"

void GuiRendererConcreteMediator::notify(EventType event)
{
    switch (event) {
        case EventType::LoadModel: {
            renderer.getSceneManager().loadModel(imguiUI.getMeshFilePath(), imguiUI.getMeshFilePathParentFolder());
            renderer.gaussianBufferFromSize(imguiUI.getResolutionTarget() * imguiUI.getResolutionTarget());
            renderer.setFormatType(0); //TODO: use an enum
            renderer.setStdDevFromImGui(imguiUI.getGaussianStd());
            renderer.setViewportResolutionForConversion(imguiUI.getResolutionTarget());

            renderer.enableRenderPass(conversionPassName);

            imguiUI.setLoadNewMesh(false);
            imguiUI.setMeshLoaded(true);
            
            imguiUI.setPlyLoaded(false); //need to reset this
            break;
        }
        case EventType::LoadPly: {
            if (renderer.getSceneManager().loadPly(imguiUI.getPlyFilePath()))
            {
                renderer.updateGaussianBuffer();
                renderer.setFormatType(1); //TODO: use an enum

                imguiUI.setLoadNewPly(false);
                imguiUI.setPlyLoaded(true);
                

                imguiUI.setMeshLoaded(false); //need to reset this
            }
            break;
        }
        case EventType::RunConversion: {
            renderer.setStdDevFromImGui(imguiUI.getGaussianStd());
            renderer.setViewportResolutionForConversion(imguiUI.getResolutionTarget());
            renderer.enableRenderPass(conversionPassName);
            imguiUI.setRunConversion(false);
            
            break;
        }
        case EventType::CheckShaderUpdate: {
            renderer.updateShadersIfNeeded();
            renderer.setLastShaderCheckTime(glfwGetTime());
            
            break;
        }
        case EventType::SavePLY: {
            renderer.getSceneManager().exportPly(imguiUI.getMeshFullFilePathDestination(), imguiUI.getFormatOption());
            imguiUI.setShouldSavePly(false);
            break;
        }
    }
}

void GuiRendererConcreteMediator::update()
{
    double currentTime = glfwGetTime();
    if (currentTime - renderer.getLastShaderCheckTime() > 1.0) {
        notify(EventType::CheckShaderUpdate);
    }

    bool windowVisible = !renderer.isWindowMinimized();

    const bool batchActive      = imguiUI.isBatchRunning();
    const bool batchHasWork     = (currentJob != nullptr) || imguiUI.hasBatchWork();

    if (windowVisible && batchActive) {
        // If batch says it's running but there's nothing to do, end it now!
        if (!batchHasWork) {
            imguiUI.cancelBatch();
            currentJob = nullptr;
            batchSubstate = BatchSubstate::Idle;
        } else {
            switch (batchSubstate) {
                case BatchSubstate::Idle: {
                    if (imguiUI.hasBatchWork()) {
                        if (ImGuiUI::BatchItem* job = imguiUI.popNextBatchItem()) {
                            startBatchJob(job, imguiUI);
                        }
                    }
                    break;
                }
                case BatchSubstate::Converting: {
                    ++framesSinceDispatch;
                    if (framesSinceDispatch >= 1) { // 2 or more if race cond in exports
                        batchSubstate = BatchSubstate::Exporting;
                    }
                    break;
                }
                case BatchSubstate::Exporting: {
                    try {
                        const unsigned int fmt = imguiUI.getFormatOption();
                        renderer.getSceneManager().exportPly(currentJob->outPath, fmt);
                        finishBatchJobSuccess(imguiUI);
                    } catch (const std::exception& e) {
                        finishBatchJobFail(imguiUI, e.what());
                    } catch (...) {
                        finishBatchJobFail(imguiUI, "Unknown error");
                    }
                    break;
                }
                case BatchSubstate::Loading:
                    break;
            }

            double gpuFrameTime = renderer.getTotalGpuFrameTimeMs();
            imguiUI.setFrameMetrics(gpuFrameTime);
            return;
        }
    }

    if(windowVisible && !batchActive)
    { 
        if (imguiUI.shouldLoadNewMesh() && !imguiUI.getMeshFilePath().empty()) {
            notify(EventType::LoadModel);
        }

        if (imguiUI.shouldLoadPly() && !imguiUI.getPlyFilePath().empty()) {
            notify(EventType::LoadPly);
        }

        if (imguiUI.shouldRunConversion() && imguiUI.wasMeshLoaded()) {
            notify(EventType::RunConversion);
        }

        if (imguiUI.shouldSavePly()) {
            notify(EventType::SavePLY);
        }
    }
    
    double gpuFrameTime = renderer.getTotalGpuFrameTimeMs(); // Retrieve GPU frame time
    imguiUI.setFrameMetrics(gpuFrameTime);
}

//TODO: as you can see batchItem should NOT be part of the ImGuiUI, this is poor SWE

static bool isGlb(utils::ModelFileExtension e) { return e == utils::ModelFileExtension::GLB; }
static bool isPly(utils::ModelFileExtension e) { return e == utils::ModelFileExtension::PLY; }

void GuiRendererConcreteMediator::startBatchJob(ImGuiUI::BatchItem* job, ImGuiUI& ui) {
    currentJob = job;
    framesSinceDispatch = 0;
    batchSubstate = BatchSubstate::Loading;

    renderer.setFormatType(0); 
    renderer.setStdDevFromImGui(ui.getGaussianStd());

    if (isGlb(job->ext)) {
        renderer.getSceneManager().loadModel(job->path, job->parent);
        renderer.gaussianBufferFromSize(ui.getResolutionTarget() * ui.getResolutionTarget());
        renderer.setViewportResolutionForConversion(ui.getResolutionTarget());
        renderer.enableRenderPass(conversionPassName);
        batchSubstate = BatchSubstate::Converting;
    } else {
        finishBatchJobFail(ui, "Unsupported extension");
    }
}


void GuiRendererConcreteMediator::finishBatchJobSuccess(ImGuiUI& ui) {
    ui.markBatchItemDone(currentJob->path);
    currentJob = nullptr;
    batchSubstate = BatchSubstate::Idle;
}

void GuiRendererConcreteMediator::finishBatchJobFail(ImGuiUI& ui, const std::string& what) {
    ui.markBatchItemFailed(currentJob->path, what);
    currentJob = nullptr;
    batchSubstate = BatchSubstate::Idle;
}
