bl_info = {
    "name": "Shader Keying Set Tools",
    "author": "ChatGPT",
    "version": (1, 3, 4),
    "blender": (2, 80, 0),
    "location": "3D View > Sidebar > Keying Sets",
    "description": "Extract, apply, and manage shader-parameter Keying Sets with per-file presets",
    "category": "Animation",
}

import bpy, json
from bpy.props import StringProperty, CollectionProperty, IntProperty
from bpy.types import PropertyGroup, Operator, Panel

# -----------------------------------------------------------------------------
# Data Structures (per-scene storage)
# -----------------------------------------------------------------------------
class ShaderPresetItem(PropertyGroup):
    name: StringProperty(name="Preset Name")
    values_json: StringProperty(name="Values JSON")

# -----------------------------------------------------------------------------
# Core Keying-Set Functions
# -----------------------------------------------------------------------------
def extract_shader_keying_set(ctx, ks_name):
    scene = ctx.scene
    ks = scene.keying_sets.get(ks_name)
    if ks:
        ks.paths.clear()
    else:
        ks = scene.keying_sets.new(name=ks_name)
    scene.keying_sets.active = ks

    mats = []
    if getattr(ctx, "selected_ids", None):
        for idb in ctx.selected_ids:
            if isinstance(idb, bpy.types.Material):
                mats.append(idb)
    if not mats:
        for ob in ctx.selected_objects:
            for slot in ob.material_slots:
                if slot.material:
                    mats.append(slot.material)
    mats = list({m.name: m for m in mats}.values())

    seen = set()
    for m in mats:
        nt = m.node_tree
        if not nt or not nt.animation_data or not nt.animation_data.action:
            continue
        for fcu in nt.animation_data.action.fcurves:
            if not fcu.keyframe_points:
                continue
            dp = f"node_tree.{fcu.data_path}"
            key = (m.name, dp, fcu.array_index)
            if key in seen:
                continue
            seen.add(key)
            ks.paths.add(target_id=m, data_path=dp, index=fcu.array_index)
    return len(ks.paths)

def apply_shader_keying_set(ctx, ks_name):
    scene = ctx.scene
    frame = scene.frame_current
    ks = scene.keying_sets.get(ks_name)
    if not ks:
        return 0
    count = 0
    for path in ks.paths:
        dp_full = path.data_path
        idx     = path.array_index
        if dp_full.startswith("node_tree."):
            nt = path.id.node_tree
            dp = dp_full[len("node_tree."):]
            nt.keyframe_insert(data_path=dp, frame=frame)
        else:
            tgt = path.id
            if idx > 0:
                tgt.keyframe_insert(data_path=dp_full, index=idx, frame=frame)
            else:
                tgt.keyframe_insert(data_path=dp_full, frame=frame)
        count += 1
    return count

# -----------------------------------------------------------------------------
# Preset Management (per-scene)
# -----------------------------------------------------------------------------
def save_current_preset(ctx, preset_name):
    scene = ctx.scene
    ks = scene.keying_sets.get(scene.ks_apply_shader_set.strip())
    if not ks:
        return None, "Select a valid Keying Set first"
    if not preset_name:
        return None, "Enter a preset name"
    data = []
    for path in ks.paths:
        m = path.id
        dp_full = path.data_path
        if dp_full.startswith("node_tree."):
            dp = dp_full[len("node_tree."):]
            val = m.node_tree.path_resolve(dp)
        else:
            val = m.path_resolve(dp_full)
        data.append({'material': m.name, 'data_path': dp_full, 'value': val})
    item = scene.shader_presets.add()
    item.name = preset_name
    item.values_json = json.dumps(data)
    scene.shader_preset_index = len(scene.shader_presets) - 1
    return item, None

def apply_shader_preset(ctx, idx):
    scene = ctx.scene
    frame = scene.frame_current
    if idx < 0 or idx >= len(scene.shader_presets):
        return False
    entries = json.loads(scene.shader_presets[idx].values_json)
    for entry in entries:
        m = bpy.data.materials.get(entry['material'])
        if not m:
            continue
        dp_full, val = entry['data_path'], entry['value']

        # Handle node-socket floats
        if dp_full.startswith("node_tree.") and dp_full.endswith(".default_value"):
            nt   = m.node_tree
            tail = dp_full[len("node_tree."):]
            base = tail[:-len(".default_value")]
            sock = nt.path_resolve(base)
            setattr(sock, 'default_value', val)
            sock.keyframe_insert(data_path='default_value', frame=frame)

        else:
            # Fallback for booleans or other props: keyframe on the material directly
            try:
                m.keyframe_insert(data_path=dp_full, frame=frame)
            except Exception:
                pass

    return True

# -----------------------------------------------------------------------------
# Operators
# -----------------------------------------------------------------------------
class KS_OT_ExtractShader(Operator):
    bl_idname = "ks.extract_shader"
    bl_label  = "Extract Keying Set"
    def execute(self, ctx):
        name = ctx.scene.ks_new_shader_set.strip()
        if not name:
            self.report({'ERROR'}, "Enter a Keying Set name")
            return {'CANCELLED'}
        cnt = extract_shader_keying_set(ctx, name)
        self.report({'INFO'}, f"KS '{name}' has {cnt} paths")
        return {'FINISHED'}

class KS_OT_ApplyShader(Operator):
    bl_idname = "ks.apply_shader"
    bl_label  = "Apply Keying Set"
    def execute(self, ctx):
        name = ctx.scene.ks_apply_shader_set.strip()
        cnt  = apply_shader_keying_set(ctx, name)
        if cnt == 0:
            self.report({'ERROR'}, f"No keys from '{name}'")
            return {'CANCELLED'}
        self.report({'INFO'}, f"Inserted {cnt} keys from '{name}'")
        return {'FINISHED'}

class KS_OT_SavePreset(Operator):
    bl_idname = "ks.save_preset"
    bl_label  = "Save Preset"
    def execute(self, ctx):
        sn = ctx.scene.ks_preset_name.strip()
        item, err = save_current_preset(ctx, sn)
        if err:
            self.report({'ERROR'}, err)
            return {'CANCELLED'}
        ctx.scene.ks_preset_name = ""
        self.report({'INFO'}, f"Preset '{item.name}' saved")
        return {'FINISHED'}

class KS_OT_RemovePreset(Operator):
    bl_idname = "ks.remove_preset"
    bl_label  = "Remove Preset"
    def execute(self, ctx):
        s, i = ctx.scene, ctx.scene.shader_preset_index
        if i < 0 or i >= len(s.shader_presets):
            return {'CANCELLED'}
        s.shader_presets.remove(i)
        s.shader_preset_index = min(i, len(s.shader_presets)-1)
        return {'FINISHED'}

class KS_OT_ApplyPreset(Operator):
    bl_idname = "ks.apply_preset"
    bl_label  = "Apply Preset"
    def execute(self, ctx):
        s = ctx.scene
        if not apply_shader_preset(ctx, s.shader_preset_index):
            self.report({'ERROR'}, "Failed to apply preset")
            return {'CANCELLED'}
        self.report({'INFO'}, f"Applied '{s.shader_presets[s.shader_preset_index].name}'")
        return {'FINISHED'}

class KS_OT_BatchCollect(Operator):
    bl_idname = "ks.batch_collect"
    bl_label  = "Batch Collect"
    def execute(self, ctx):
        s, cnt = ctx.scene, 0
        for ks in s.keying_sets:
            data = []
            for path in ks.paths:
                m, dp = path.id, path.data_path
                if dp.startswith("node_tree."):
                    v = m.node_tree.path_resolve(dp[len("node_tree."):])
                else:
                    v = m.path_resolve(dp)
                data.append({'material':m.name,'data_path':dp,'value':v})
            itm = s.shader_presets.add()
            itm.name = ks.name + "_preset"
            itm.values_json = json.dumps(data)
            cnt += 1
        s.shader_preset_index = len(s.shader_presets)-1
        self.report({'INFO'}, f"Collected {cnt} presets")
        return {'FINISHED'}

# -----------------------------------------------------------------------------
# UI Panel
# -----------------------------------------------------------------------------
class KS_PT_Panel(Panel):
    bl_label      = "Shader Keying Sets"
    bl_space_type = 'VIEW_3D'
    bl_region_type= 'UI'
    bl_category   = 'Keying Sets'

    def draw(self, ctx):
        l, s = self.layout, ctx.scene

        box = l.box()
        box.label(text="Extract Keying Set")
        box.prop(s, "ks_new_shader_set", text="Name")
        box.operator("ks.extract_shader", icon='IMPORT')

        box = l.box()
        box.label(text="Apply Keying Set")
        box.prop_search(s, "ks_apply_shader_set", s, "keying_sets", text="Set")
        box.operator("ks.apply_shader", icon='KEYFRAME')

        box = l.box()
        box.label(text="Save Current Values as Preset")
        box.prop(s, "ks_preset_name", text="Preset Name")
        box.operator("ks.save_preset", icon='ADD')

        box = l.box()
        box.label(text="Manage Shader Presets")
        row = box.row()
        row.template_list("UI_UL_list", "preset_list_id",
                          s, "shader_presets",
                          s, "shader_preset_index",
                          rows=4)
        col = row.column(align=True)
        col.operator("ks.remove_preset", icon='REMOVE', text="")
        col.operator("ks.batch_collect", icon='RECOVER_LAST', text="Batch")
        box.operator("ks.apply_preset", icon='FILE_TICK', text="Apply")

# -----------------------------------------------------------------------------
# Registration
# -----------------------------------------------------------------------------
classes = (
    ShaderPresetItem, KS_OT_ExtractShader, KS_OT_ApplyShader,
    KS_OT_SavePreset, KS_OT_RemovePreset, KS_OT_ApplyPreset,
    KS_OT_BatchCollect, KS_PT_Panel,
)

def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.Scene.ks_new_shader_set    = StringProperty(name="Keying Set Name", default="")
    bpy.types.Scene.ks_apply_shader_set  = StringProperty(name="Apply Set",      default="")
    bpy.types.Scene.ks_preset_name      = StringProperty(name="Preset Name",   default="")
    bpy.types.Scene.shader_presets      = CollectionProperty(type=ShaderPresetItem)
    bpy.types.Scene.shader_preset_index = IntProperty(default=0)

def unregister():
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
    del bpy.types.Scene.ks_new_shader_set
    del bpy.types.Scene.ks_apply_shader_set
    del bpy.types.Scene.ks_preset_name
    del bpy.types.Scene.shader_presets
    del bpy.types.Scene.shader_preset_index

if __name__ == "__main__":
    register()
