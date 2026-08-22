///////////////////////////////////////////////////////////////////////////////
//         Mesh2Splat: fast mesh to 3D gaussian splat conversion             //
//        Copyright (c) 2025 Electronic Arts Inc. All rights reserved.       //
///////////////////////////////////////////////////////////////////////////////

#pragma once

#include <cstdint>
#include <string>

namespace conversion {

/** Matches std430 layout written by converterFS.glsl (binding 2). */
struct ConversionGaussianMeta {
    uint32_t plyGaussianIndex;
    float glFragCoordX;
    float glFragCoordY;
    uint32_t triangleId;
};
static_assert(sizeof(ConversionGaussianMeta) == 16u, "Keep in sync with conversion shader std430 struct");

/** Derive companion path: path/to/out.ply -> path/to/out.csv */
std::string plyOutputPathToCsvPath(const std::string& plyPath);

/**
 * Writes one CSV row per gaussian matching PLY vertex order / SSBO indices.
 * Header comments document resolutionTarget, node count, indexing convention.
 */
void exportGaussianPixelTableCsv(
    const std::string& csvPath,
    unsigned int resolutionTarget,
    int gaussianCount,
    const ConversionGaussianMeta* metaRows);

} // namespace conversion
