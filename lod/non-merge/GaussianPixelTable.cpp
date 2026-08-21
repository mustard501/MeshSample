///////////////////////////////////////////////////////////////////////////////
//         Mesh2Splat: fast mesh to 3D gaussian splat conversion             //
//        Copyright (c) 2025 Electronic Arts Inc. All rights reserved.       //
///////////////////////////////////////////////////////////////////////////////

#include "lod/non-merge/GaussianPixelTable.hpp"

#include <fstream>
#include <iomanip>

namespace lod {

std::string plyOutputPathToCsvPath(const std::string& plyPath)
{
    static const std::string kExt = ".ply";
    if (plyPath.size() >= kExt.size()
        && plyPath.compare(plyPath.size() - kExt.size(), kExt.size(), kExt) == 0)
    {
        return plyPath.substr(0, plyPath.size() - kExt.size()) + ".csv";
    }
    return plyPath + ".csv";
}

void exportGaussianPixelTableCsv(
    const std::string& csvPath,
    const unsigned int resolutionTarget,
    const int gaussianCount,
    const ConversionGaussianMeta* metaRows)
{
    if (!metaRows || gaussianCount <= 0)
        return;

    std::ofstream out(csvPath, std::ios::out | std::ios::trunc);
    if (!out)
        return;

    out << std::setprecision(9) << std::fixed;
    out << "# Mesh2Splat conversion pixel table (LOD aux)\n";
    out << "# resolutionTarget=" << resolutionTarget << "\n";
    out << "# nodeCount=" << gaussianCount << "\n";
    out << "# plyGaussianIndex: 0-based row index, same order as vertices in the exported .ply\n";
    out << "# gl_FragCoord.x/y: built-in fragment coordinates (OpenGL default, origin bottom-left)\n";
    out << "# triangleId: primitive index within the mesh draw call (single-mesh workflows; resets per mesh draw)\n";
    out << "plyGaussianIndex,gl_FragCoord.x,gl_FragCoord.y,triangleId\n";

    for (int i = 0; i < gaussianCount; ++i)
    {
        const ConversionGaussianMeta& m = metaRows[i];
        out << m.plyGaussianIndex << ',' << m.glFragCoordX << ',' << m.glFragCoordY << ',' << m.triangleId << '\n';
    }
}

} // namespace lod
