import json
import math
import os.path as osp
import random
import sys
import tempfile

# Blender modules
import bpy
import mathutils


# -----------------------------------------------------------------------------
# --------------------------------- Constants ---------------------------------
# -----------------------------------------------------------------------------
PI = math.pi
EPS = sys.float_info.epsilon
PREFIX = 'BNGP'


# -----------------------------------------------------------------------------
# -------------------------------- Addon Info ---------------------------------
# -----------------------------------------------------------------------------
bl_info = {
    'name': 'Blender-NGP',
    'author': 'takiyu',
    'version': (0, 1),
    'blender': (3, 0, 0),
    'location': 'Properties',
    'description': 'Blender + Instant-NGP',
}


# -----------------------------------------------------------------------------
# ------------------------------ Blender Utility ------------------------------
# -----------------------------------------------------------------------------
def fetch_active_object(context):
    return context.view_layer.objects.active


def set_active_object(context, obj):
    context.view_layer.objects.active = obj


def fetch_selected_objects(context):
    return context.selected_objects


def set_selected_objects(objs):
    bpy.ops.objects.select_all(action='DESELECT')
    for obj in objs:
        obj.select_set(True)


def fetch_active_camera(context):
    return context.scene.camera


def set_active_camera(context, cam_obj):
    context.scene.camera = cam_obj


def fetch_default_coll(context):
    return context.scene.collection


def create_collection(context, name, parent_coll=None):
    if parent_coll is None:
        parent_coll = fetch_default_coll(context)

    # Create new collection
    coll = bpy.data.collections.new(name)
    parent_coll.children.link(coll)  # Link
    return coll


def create_cam_obj(context, name, parent_coll=None):
    if parent_coll is None:
        parent_coll = fetch_default_coll(context)

    # Create new collection
    cam = bpy.data.cameras.new(name)
    cam_obj = bpy.data.objects.new(name, cam)
    parent_coll.objects.link(cam_obj)  # Link
    return cam_obj


def collect_by_name_prefix(data_coll, prefix):
    # Collect name-matched objects
    collected_objs = list()
    for obj in data_coll:
        if obj.name.startswith(prefix):
            collected_objs.append(obj)
    return collected_objs


def remove_by_name_prefix(data_coll, prefix):
    # Collect
    objs = collect_by_name_prefix(data_coll, prefix)
    # Remove
    for obj in objs:
        try:
            data_coll.remove(obj, do_unlink=True)
        except Exception:
            data_coll.remove(obj)


def add_dropdown_ui(layout, props, prop_name, text):
    # Decide on/off icon
    is_active = getattr(props, prop_name)
    icon = 'TRIA_DOWN' if is_active else 'TRIA_RIGHT'

    # Create drop-down title
    box = layout.box()
    row = box.row()
    row.prop(props, prop_name, icon=icon, icon_only=True)
    row.label(text=text)

    # Create drop-down box
    if is_active:
        row = box.row()
        row.column()  # Indent
        col = row.column()
        return col
    else:
        return None


def pass_props(src_props, tgt_props):
    for name in dir(src_props):
        if name.startswith(('_', 'bl_', 'rna_type')):
            continue  # Skip hidden properties
        if hasattr(tgt_props, name):
            # Pass through
            prop = getattr(src_props, name)
            setattr(tgt_props, name, prop)


# -----------------------------------------------------------------------------
# ----------------------------- Camera Generation -----------------------------
# -----------------------------------------------------------------------------
class BNGP_OT_ExecCamGeneration(bpy.types.Operator):
    bl_idname = 'bngp.exec_cam_generation'
    bl_label = 'Generate cameras'
    bl_options = {'REGISTER', 'UNDO'}

    n_cams_holi: bpy.props.IntProperty(options={'HIDDEN'})
    n_cams_vert: bpy.props.IntProperty(options={'HIDDEN'})
    cam_dist: bpy.props.FloatProperty(options={'HIDDEN'})
    cam_fov: bpy.props.FloatProperty(options={'HIDDEN'})

    def execute(self, context):
        # Fetch target object
        tgt_obj = fetch_active_object(context)
        assert(tgt_obj)
        center_pos = tgt_obj.location

        # Create collection
        cam_coll_name = f'{PREFIX}__cam_coll'
        cam_coll = create_collection(context, cam_coll_name)

        # Create cameras
        n_cams = self.n_cams_vert * self.n_cams_holi
        for c_idx in range(n_cams):
            holi_idx = c_idx % self.n_cams_holi
            vert_idx = c_idx // self.n_cams_holi

            # Create camera object
            cam_name = f'{PREFIX}__cam_{c_idx:03}'
            cam_obj = create_cam_obj(context, cam_name, cam_coll)

            # Set camera location
            phi = 2.0 * PI * holi_idx / self.n_cams_holi
            theta = PI * (vert_idx + 1) / (self.n_cams_vert + 1)
            unit_loc = mathutils.Vector([math.sin(theta) * math.cos(phi),
                                         math.sin(theta) * math.sin(phi),
                                         math.cos(theta)])
            cam_obj.location = unit_loc * self.cam_dist + center_pos
            # Set camera direction
            direction = center_pos - cam_obj.location
            cam_obj. rotation_mode = 'QUATERNION'
            cam_obj.rotation_quaternion = direction.to_track_quat('-Z', 'Y')

            # Set camera FOV (degree -> radian)
            cam_obj.data.lens_unit = 'FOV'
            cam_obj.data.angle = math.radians(self.cam_fov)

        return {'FINISHED'}


class BNGP_OT_ExecCamClear(bpy.types.Operator):
    bl_idname = 'bngp.exec_cam_clear'
    bl_label = 'Clear cameras'
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        # Remove cameras & collections
        remove_by_name_prefix(bpy.data.cameras, PREFIX)
        remove_by_name_prefix(bpy.data.collections, PREFIX)
        return {'FINISHED'}


# -----------------------------------------------------------------------------
# --------------------------------- Rendering ---------------------------------
# -----------------------------------------------------------------------------
class BNGP_OT_ExecRender(bpy.types.Operator):
    bl_idname = 'bngp.exec_render'
    bl_label = 'Render'
    bl_options = {'REGISTER', 'UNDO'}

    cam_fov: bpy.props.FloatProperty(options={'HIDDEN'})
    render_width: bpy.props.IntProperty(options={'HIDDEN'})
    render_height: bpy.props.IntProperty(options={'HIDDEN'})

    def execute(self, context):
        # Fetch camera objects
        cam_objs = collect_by_name_prefix(bpy.data.objects, f'{PREFIX}__cam')
        if len(cam_objs) == 0:
            self.report({'ERROR'}, 'No camera objects found')
            return {'CANCELED'}

        # Set resolution
        context.scene.render.resolution_x = self.render_width
        context.scene.render.resolution_y = self.render_height
        # Set file format
        context.scene.render.image_settings.file_format = 'PNG'
        context.scene.render.image_settings.color_mode = 'RGBA'
        context.scene.render.image_settings.color_depth = '8'
        context.scene.render.film_transparent = True

        # Create output directory
        out_dirname = tempfile.mkdtemp(prefix=f'{PREFIX}__')
        self.report({'INFO'}, f'Output directory: {out_dirname}')

        # Create 'transforms.json'
        trans_dict = dict()
        trans_dict['camera_angle_x'] = math.radians(self.cam_fov)
        trans_dict['frames'] = list()

        # Render
        for cam_obj in cam_objs:
            # Set active camera
            set_active_camera(context, cam_obj)
            # Set output filename
            img_basename = cam_obj.name + '.png'
            context.scene.render.filepath = osp.join(out_dirname, img_basename)

            # Collect frame information
            frame_dict = dict()
            frame_dict['file_path'] = osp.join('./', img_basename)
            frame_dict['transform_matrix'] = \
                    [list(row) for row in cam_obj.matrix_world.row]
            trans_dict['frames'].append(frame_dict)

            # Render
            bpy.ops.render.render(write_still=True)

        # Save 'transforms.json'
        json_filename = osp.join(out_dirname, 'transforms.json')
        with open(json_filename, 'w') as f:
            json.dump(trans_dict, f, indent=4, ensure_ascii=True)

        return {'FINISHED'}


# -----------------------------------------------------------------------------
# -------------------------------- Main Panel ---------------------------------
# -----------------------------------------------------------------------------
class BNGP_PT_MainPanel(bpy.types.Panel):
    bl_label = 'Blender-NGP'
    bl_space_type = 'VIEW_3D'
    bl_category = 'Blender-NGP'
    bl_region_type = 'UI'

    def draw(self, context):
        layout = self.layout
        props = context.scene.bngp_props

        # Title
        layout.label(text='Blender-NGP', icon='BLENDER')

        # Camera UI
        box = add_dropdown_ui(layout, props, 'ui_cam', 'Camera Generation')
        if box:
            # Fetch target object
            tgt_obj = fetch_active_object(context)
            tgt_obj_name = tgt_obj.name if tgt_obj else '-'

            # Properties
            row = box.row()
            row.label(text=f'Target: "{tgt_obj_name}"')
            row = box.row()
            row.prop(props, 'n_cams_holi', text='Horizontal number')
            row.prop(props, 'n_cams_vert', text='Vertical number')
            row = box.row()
            row.prop(props, 'cam_dist', text='Camera distance')
            row = box.row()
            row.prop(props, 'cam_fov', text='FOV')
            row = box.row()
            box.separator()
            # Generation button
            row = box.row()
            row.enabled = bool(tgt_obj)
            child_props = row.operator(BNGP_OT_ExecCamGeneration.bl_idname,
                                       icon='VIEW_CAMERA')
            pass_props(props, child_props)
            # Clear button
            row = box.row()
            row.operator(BNGP_OT_ExecCamClear.bl_idname, icon='TRASH')

        # Render UI
        box = add_dropdown_ui(layout, props, 'ui_render', 'Rendering')
        if box:
            # Properties
            row = box.row()
            row.prop(props, 'render_width', text='Width')
            row.prop(props, 'render_height', text='Height')
            # Render button
            row = box.row()
            child_props = row.operator(BNGP_OT_ExecRender.bl_idname,
                                       icon='OUTPUT')
            pass_props(props, child_props)



# -----------------------------------------------------------------------------
# -------------------------------- Properties ---------------------------------
# -----------------------------------------------------------------------------
class BNGP_Props(bpy.types.PropertyGroup):
    # Camera
    ui_cam: bpy.props.BoolProperty(default=True)
    n_cams_holi: bpy.props.IntProperty(default=8, min=1)
    n_cams_vert: bpy.props.IntProperty(default=3, min=1)
    cam_dist: bpy.props.FloatProperty(default=5.0, min=EPS)
    cam_fov: bpy.props.FloatProperty(default=40.0, min=EPS, max=180.0)
    # Render
    ui_render: bpy.props.BoolProperty(default=True)
    render_width: bpy.props.IntProperty(default=512, min=1)
    render_height: bpy.props.IntProperty(default=512, min=1)


# -----------------------------------------------------------------------------
# ---------------------------- Addon Registration -----------------------------
# -----------------------------------------------------------------------------
CLASSES = [
    BNGP_OT_ExecCamGeneration,
    BNGP_OT_ExecCamClear,
    BNGP_OT_ExecRender,
    BNGP_PT_MainPanel,
    BNGP_Props,
]


def register():
    # Register classes
    for c in CLASSES:
        bpy.utils.register_class(c)

    # Register properties
    bpy.types.Scene.bngp_props = \
        bpy.props.PointerProperty(type=BNGP_Props)


def unregister():
    # Un-register properties
    del bpy.types.Scene.bngp_props

    # Un-register classes
    for c in CLASSES:
        bpy.utils.unregister_class(c)


if __name__ == '__main__':
    register()
# -----------------------------------------------------------------------------
# -----------------------------------------------------------------------------
# -----------------------------------------------------------------------------
