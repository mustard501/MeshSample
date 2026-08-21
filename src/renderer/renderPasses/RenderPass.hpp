///////////////////////////////////////////////////////////////////////////////
//         Mesh2Splat: fast mesh to 3D gaussian splat conversion             //
//        Copyright (c) 2025 Electronic Arts Inc. All rights reserved.       //
///////////////////////////////////////////////////////////////////////////////

#pragma once
#include "RenderContext.hpp"

#define MAX_GAUSSIANS_TO_SORT 7000000 

class IRenderPass {
public:
    virtual ~IRenderPass() = default;
    virtual void execute(RenderContext& context) = 0;
    virtual bool isEnabled() const { return isPassEnabled; }
    virtual void setIsEnabled(bool isPassEnabled) { this->isPassEnabled = isPassEnabled; };

private:
    bool isPassEnabled = false;

};