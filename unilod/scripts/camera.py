import bpy
import math
from mathutils import Vector

SPHERE_RADIUS = 2
FRAMES_PER_CIRCLE = 50
NUM_CIRCLES = 5
TOTAL_FRAMES = FRAMES_PER_CIRCLE * NUM_CIRCLES
RESOLUTION = 800

# 5 个关于 xy 平面对称的截面高度（|z| < R）
Z_HEIGHTS = [-1.5, -1.0, 0.0, 1.0, 1.5]
# 空轴 z 向原点收缩比例：保持与圆的高度对应，但更瞄准场景中心
TARGET_Z_SCALE = 0.35

CAMERA_NAME = "Camera_SpherePath"
EMPTY_PREFIX = "Empty_Target_"
SPHERE_REF_NAME = "Sphere_Ref_2.5"


def circle_radius_at_z(z, radius=SPHERE_RADIUS):
    return math.sqrt(max(radius * radius - z * z, 0.0))


def target_location_for_circle(z):
    return Vector((0.0, 0.0, z * TARGET_Z_SCALE))


def point_camera_at(camera, target):
    direction = Vector(target) - camera.location
    if direction.length < 1e-8:
        return
    camera.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()


def remove_if_exists(name):
    obj = bpy.data.objects.get(name)
    if obj:
        bpy.data.objects.remove(obj, do_unlink=True)


def setup_scene():
    scene = bpy.context.scene
    scene.frame_start = 1
    scene.frame_end = TOTAL_FRAMES
    scene.render.resolution_x = RESOLUTION
    scene.render.resolution_y = RESOLUTION
    scene.render.resolution_percentage = 100


def create_reference_sphere():
    remove_if_exists(SPHERE_REF_NAME)
    bpy.ops.mesh.primitive_uv_sphere_add(
        radius=SPHERE_RADIUS,
        location=(0.0, 0.0, 0.0),
        segments=64,
        ring_count=32,
    )
    sphere = bpy.context.active_object
    sphere.name = SPHERE_REF_NAME
    sphere.display_type = "WIRE"
    sphere.hide_render = True
    return sphere


def create_targets():
    targets = []
    for i, z in enumerate(Z_HEIGHTS):
        name = "%s%02d" % (EMPTY_PREFIX, i + 1)
        remove_if_exists(name)
        empty = bpy.data.objects.new(name, None)
        empty.empty_display_type = "PLAIN_AXES"
        empty.empty_display_size = 0.35
        empty.location = target_location_for_circle(z)
        bpy.context.scene.collection.objects.link(empty)
        targets.append(empty)
    return targets


def create_camera():
    remove_if_exists(CAMERA_NAME)
    cam_data = bpy.data.cameras.new(CAMERA_NAME)
    camera = bpy.data.objects.new(CAMERA_NAME, cam_data)
    bpy.context.scene.collection.objects.link(camera)
    bpy.context.scene.camera = camera
    return camera


def animate_camera(camera, targets):
    if camera.animation_data:
        camera.animation_data_clear()

    for circle_idx, z in enumerate(Z_HEIGHTS):
        r = circle_radius_at_z(z)
        target = targets[circle_idx].location
        start_frame = circle_idx * FRAMES_PER_CIRCLE + 1
        end_frame = start_frame + FRAMES_PER_CIRCLE - 1

        for local_frame in range(FRAMES_PER_CIRCLE):
            frame = start_frame + local_frame
            angle = (local_frame / FRAMES_PER_CIRCLE) * 2.0 * math.pi
            camera.location = (r * math.cos(angle), r * math.sin(angle), z)
            point_camera_at(camera, target)
            camera.keyframe_insert(data_path="location", frame=frame)
            camera.keyframe_insert(data_path="rotation_euler", frame=frame)

        # 闭合圆：最后一帧与第一帧位置一致，便于循环预览
        if circle_idx < NUM_CIRCLES - 1:
            next_start = end_frame + 1
            next_z = Z_HEIGHTS[circle_idx + 1]
            next_r = circle_radius_at_z(next_z)
            camera.location = (next_r, 0.0, next_z)
            point_camera_at(camera, targets[circle_idx + 1].location)
            camera.keyframe_insert(data_path="location", frame=next_start)
            camera.keyframe_insert(data_path="rotation_euler", frame=next_start)


def main():
    setup_scene()
    create_reference_sphere()
    targets = create_targets()
    camera = create_camera()
    animate_camera(camera, targets)

    bpy.context.scene.frame_set(1)
    print(
        "相机轨迹完成: %d 帧, %dx%d, 圆高度=%s, 空轴高度=%s"
        % (
            TOTAL_FRAMES,
            RESOLUTION,
            RESOLUTION,
            Z_HEIGHTS,
            [round(z * TARGET_Z_SCALE, 3) for z in Z_HEIGHTS],
        )
    )


main()
