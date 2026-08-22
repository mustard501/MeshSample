///////////////////////////////////////////////////////////////////////////////
//         Mesh2Splat: fast mesh to 3D gaussian splat conversion             //
//        Copyright (c) 2025 Electronic Arts Inc. All rights reserved.       //
///////////////////////////////////////////////////////////////////////////////

#version 460 core

//change layout and use different outs
uniform sampler2D albedoTexture;
uniform sampler2D normalTexture;
uniform sampler2D metallicRoughnessTexture;
uniform sampler2D occlusionTexture;
uniform sampler2D emissiveTexture;

uniform int hasAlbedoMap;
uniform int hasNormalMap;
uniform int hasMetallicRoughnessMap;
uniform vec4 u_materialFactor;
uniform vec2 u_metallicRoughnessFactor;
uniform float u_ior;
uniform int u_maxGaussians;

struct GaussianVertex {
    vec4 position;
    vec4 color;
    vec4 scale;
    vec4 normal;
    vec4 rotation;
    vec4 pbr;
};

/** std430 mirrors C++ lod::ConversionGaussianMeta */
struct ConversionMetaRecord {
    uint plyGaussianIndex;
    float glFragCoordX;
    float glFragCoordY;
    uint triangleId;
};

layout(std430, binding = 0) buffer GaussianBuffer {
    GaussianVertex vertices[];
} gaussianBuffer;

layout(std430, binding = 2) buffer ConversionMetaBuffer {
    ConversionMetaRecord records[];
} conversionMetaBuffer;

layout(binding = 1) uniform atomic_uint g_validCounter;

// Inputs from the geometry shader
in vec3 Position;
in vec3 Scale;
in vec2 UV;
in vec4 Tangent;
in vec3 Normal;
in vec4 Quaternion;
flat in int vPrimitiveId;

void main() {
    
    uint index = atomicCounterIncrement(g_validCounter);

    // Bounds check: discard if we've exceeded the buffer capacity
    if (index >= uint(u_maxGaussians)) {
        discard;
    }

    // glTF: sampled baseColor RGB is sRGB-encoded in the asset; multiply with linear baseColorFactor in linear-light space.
    // Albedo texture is uploaded as GL_*SRGB* so texture() yields linear RGB; alpha stays linear per GL spec.
    vec4 albedoLin;
    if (hasAlbedoMap == 1)
    {
        vec4 tex = texture(albedoTexture, UV);
        albedoLin = vec4(tex.rgb * u_materialFactor.rgb, tex.a * u_materialFactor.a);
    }
    else
    {
        albedoLin = u_materialFactor;
    }

    //NORMAL MAP
    //Should compute the TBN in geometry shader
    vec3 out_Normal;

    if (hasNormalMap == 1)
    {
        vec3 normalMap_normal = texture(normalTexture, UV).xyz;
        vec3 retrievedNormal = normalize(normalMap_normal.xyz * 2.0f - 1.0f); 

        vec3 bitangent = normalize(cross(Normal, Tangent.xyz)) * Tangent.w;
        mat3 TBN = mat3(Tangent.xyz, bitangent, normalize(Normal));

        out_Normal = normalize(TBN * retrievedNormal); //in model space

    }
    else {
        out_Normal = Normal;
    }

    


    // METALLIC-ROUGHNESS: glTF metallic = metallicFactor * texture.b, roughness = roughnessFactor * texture.g (texture=1 if omitted)
    vec2 metallicRoughness;
    if (hasMetallicRoughnessMap == 1)
    {
        vec2 sampled = texture(metallicRoughnessTexture, UV).bg;
        metallicRoughness = vec2(
            sampled.x * u_metallicRoughnessFactor.x,
            sampled.y * u_metallicRoughnessFactor.y);
    }
    else {
        metallicRoughness = u_metallicRoughnessFactor;
    }

    // Pack Gaussian parameters into the output fragments
    gaussianBuffer.vertices[index].position = vec4(Position.xyz, 1);
    gaussianBuffer.vertices[index].color = clamp(albedoLin, 0.0, 1.0);
    gaussianBuffer.vertices[index].scale = vec4(Scale, 0.0);
    gaussianBuffer.vertices[index].normal = vec4(out_Normal, 0.0);
    gaussianBuffer.vertices[index].rotation = Quaternion;
    gaussianBuffer.vertices[index].pbr = vec4(metallicRoughness, u_ior, 1.0);

    conversionMetaBuffer.records[index].plyGaussianIndex = index;
    conversionMetaBuffer.records[index].glFragCoordX = gl_FragCoord.x;
    conversionMetaBuffer.records[index].glFragCoordY = gl_FragCoord.y;
    conversionMetaBuffer.records[index].triangleId = uint(max(vPrimitiveId, 0));
}
