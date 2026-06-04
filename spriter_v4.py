import os
import subprocess
import glob
import shutil
import sys
from PIL import Image

# ==============================================================================
# 1. GLOBALE KONFIGURATION - HIER ALLES EINSTELLEN
# ==============================================================================
BLENDER_PATH = r"D:\Blender\blender.exe"
MODEL_PATH = r"player_1.glb"
OUTPUT_DIR = r"output"

# Frame-Einstellungen
FRAME_WIDTH = 16
FRAME_HEIGHT = 32
FPS = 8

# Automatischer Zoom & Zentrierung
AUTO_ZOOM = True           # Wenn True, ignoriert das Skript CAMERA_ORTHO_SCALE und berechnet es selbst
ZOOM_MARGIN = 0.5        # 1.1 bedeutet 10% Platz zum Rand. 1.0 würde bedeuten, die Figur berührt den Rand.
CAMERA_ORTHO_SCALE = 2.5   # Wird nur verwendet, wenn AUTO_ZOOM = False ist.

# Kamera-Einstellungen
CAMERA_ANGLE_X = 45.0      # Winkel von oben (z.B. 45 für Isometrisch, 90 für reines Top-Down)

# Welche Animationen aus Blockbench sollen exportiert werden?
ANIMATIONS = ["walking"]
# ==============================================================================


TEMP_DIR = os.path.join(OUTPUT_DIR, "_temp_render_frames")
BLENDER_SCRIPT_PATH = os.path.join(OUTPUT_DIR, "_temp_blender_script.py")

def check_paths():
    if not os.path.exists(BLENDER_PATH):
        print(f"FEHLER: Blender wurde unter '{BLENDER_PATH}' nicht gefunden!")
        print("Bitte überprüfe den BLENDER_PATH in der Konfiguration.")
        sys.exit(1)
    if not os.path.exists(MODEL_PATH):
        print(f"FEHLER: Das 3D-Modell unter '{MODEL_PATH}' wurde nicht gefunden!")
        sys.exit(1)

    # Alten Temp-Ordner vorab löschen, damit keine alten Frames die Erkennung stören
    if os.path.exists(TEMP_DIR):
        print(f"-> Lösche alten Temp-Ordner: {TEMP_DIR}")
        shutil.rmtree(TEMP_DIR)

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    os.makedirs(TEMP_DIR, exist_ok=True)

def generate_blender_script():
    """Generiert das temporäre Python-Skript, das Blender im Headless-Modus ausführt."""

    # MODEL_PATH als absoluter Pfad, damit Blender ihn immer findet
    abs_model_path = os.path.abspath(MODEL_PATH)
    abs_temp_dir   = os.path.abspath(TEMP_DIR)

    script_content = f"""import bpy
import math
import os
import sys
import mathutils

MODEL_PATH = r"{abs_model_path}"
OUTPUT_DIR = r"{abs_temp_dir}"
FRAME_WIDTH = {FRAME_WIDTH}
FRAME_HEIGHT = {FRAME_HEIGHT}
FPS = {FPS}
AUTO_ZOOM = {AUTO_ZOOM}
ZOOM_MARGIN = {ZOOM_MARGIN}
CAMERA_ANGLE_X = {CAMERA_ANGLE_X}
CAMERA_ORTHO_SCALE = {CAMERA_ORTHO_SCALE}
ANIMATIONS_TO_RENDER = {ANIMATIONS}

# Flush-Helper
def log(msg):
    print(msg, flush=True)

# Szene komplett leeren
for obj in list(bpy.context.scene.objects):
    bpy.data.objects.remove(obj, do_unlink=True)
for action in list(bpy.data.actions):
    bpy.data.actions.remove(action)

scene = bpy.context.scene

# Render-Einstellungen
scene.render.engine = 'BLENDER_EEVEE'
scene.render.film_transparent = True
scene.render.resolution_x = FRAME_WIDTH
scene.render.resolution_y = FRAME_HEIGHT
scene.render.fps = FPS
scene.render.image_settings.color_mode = 'RGBA'
scene.render.filter_size = 0.0

# Modell importieren
log("[Blender] Versuche Modell zu importieren: " + MODEL_PATH)
try:
    bpy.ops.import_scene.gltf(filepath=MODEL_PATH)
    log("[Blender] Import erfolgreich!")
except Exception as e:
    log("[Blender] FEHLER BEIM IMPORT: " + str(e))
    sys.exit(1)

# Haupt-Armature finden
armature = next((obj for obj in scene.objects if obj.type == 'ARMATURE'), None)
if not armature:
    armature = next((obj for obj in scene.objects), None)
if not armature:
    log("[Blender] FEHLER: Keine Objekte nach dem Import!")
    sys.exit(1)

log("[Blender] Hauptobjekt: " + armature.name)

# Pivot in der Mitte der Szene
bpy.ops.object.empty_add(type='PLAIN_AXES', location=(0, 0, 0))
pivot = bpy.context.object
pivot.name = "CameraPivot"

# ==========================================
# AUTO ZOOM & ZENTRIERUNG
# ==========================================
if AUTO_ZOOM:
    log("[Blender] Berechne Bounding Box für Auto-Zoom und Auto-Zentrierung...")
    meshes = [obj for obj in scene.objects if obj.type == 'MESH']
    if meshes:
        global_bbox = []
        for mesh in meshes:
            for corner in mesh.bound_box:
                global_bbox.append(mesh.matrix_world @ mathutils.Vector(corner))

        # Extreme finden
        max_x = max(v.x for v in global_bbox)
        min_x = min(v.x for v in global_bbox)
        max_y = max(v.y for v in global_bbox)
        min_y = min(v.y for v in global_bbox)
        max_z = max(v.z for v in global_bbox)
        min_z = min(v.z for v in global_bbox)

        # 1. Pivot zentrieren
        center_x = (max_x + min_x) / 2.0
        center_y = (max_y + min_y) / 2.0
        center_z = (max_z + min_z) / 2.0
        pivot.location = (center_x, center_y, center_z)
        log(f"[Blender] Pivot zentriert auf ({{center_x:.2f}}, {{center_y:.2f}}, {{center_z:.2f}})")

        # 2. Maximaler Drehradius in X/Y (Sicherstellen, dass Hände/Schwerter beim Drehen im Bild bleiben)
        max_r = max(math.sqrt((v.x - center_x)**2 + (v.y - center_y)**2) for v in global_bbox)

        # 3. Benötigte Sichtfeld-Größen
        view_w = 2.0 * max_r
        angle_rad = math.radians(CAMERA_ANGLE_X)
        # Die sichtbare Höhe setzt sich zusammen aus der reinen Z-Höhe plus der projizierten Tiefe
        view_h = (max_z - min_z) * math.cos(angle_rad) + 2.0 * max_r * math.sin(angle_rad)

        aspect_ratio = FRAME_WIDTH / FRAME_HEIGHT
        
        # Blender Ortho-Scale kalkulieren (definiert die GRÖSSTE Dimension der Kamera)
        if FRAME_WIDTH >= FRAME_HEIGHT:
            target_scale = max(view_w, view_h * aspect_ratio)
        else:
            target_scale = max(view_h, view_w / aspect_ratio)

        final_scale = target_scale * ZOOM_MARGIN
        log(f"[Blender] Auto-Zoom errechnet Scale: {{final_scale:.2f}}")
    else:
        final_scale = CAMERA_ORTHO_SCALE
        log("[Blender] Keine Meshes gefunden. Nutze Fallback-Scale.")
else:
    final_scale = CAMERA_ORTHO_SCALE

# Kamera anlegen
cam_distance = 10.0
cam_height   = cam_distance * math.tan(math.radians(CAMERA_ANGLE_X))
bpy.ops.object.camera_add(location=(0, -cam_distance, cam_height))
camera = bpy.context.object
camera.data.type = 'ORTHO'
camera.data.ortho_scale = final_scale
camera.rotation_euler = (math.radians(CAMERA_ANGLE_X), 0, 0)
camera.parent = pivot
scene.camera = camera

# Licht anlegen
bpy.ops.object.light_add(type='SUN', location=(0, -cam_distance, cam_height + 5))
sun = bpy.context.object
sun.data.energy = 2.0
sun.rotation_euler = (math.radians(CAMERA_ANGLE_X), 0, 0)
sun.parent = pivot

# Render-Schleife
if not armature.animation_data:
    armature.animation_data_create()

for anim_name in ANIMATIONS_TO_RENDER:
    action = next((a for a in bpy.data.actions if anim_name.lower() in a.name.lower()), None)
    if not action:
        log(f"[Blender] [!] Animation '{{anim_name}}' nicht gefunden – übersprungen.")
        continue

    armature.animation_data.action = action
    frame_start = int(action.frame_range[0])
    frame_end   = int(action.frame_range[1])
    total_frames = frame_end - frame_start + 1
    total_renders = total_frames * 8

    log(f"[Blender] Starte '{{anim_name}}': {{total_frames}} Frames x 8 Richtungen = {{total_renders}} Renders")

    rendered = 0
    for dir_index in range(8):
        pivot.rotation_euler[2] = math.radians(dir_index * 45)
        bpy.context.view_layer.update()

        for frame in range(frame_start, frame_end + 1):
            scene.frame_set(frame)
            filename = f"{{anim_name}}_dir{{dir_index}}_frame{{frame - frame_start:04d}}.png"
            scene.render.filepath = os.path.join(OUTPUT_DIR, filename)
            bpy.ops.render.render(write_still=True)
            rendered += 1
            if rendered % 10 == 0 or rendered == total_renders:
                log(f"[Blender]   {{anim_name}} Fortschritt: {{rendered}}/{{total_renders}}")

log("[Blender] Alle Animationen gerendert.")
"""
    with open(BLENDER_SCRIPT_PATH, "w", encoding="utf-8") as f:
        f.write(script_content)

def run_blender():
    print("-> Starte Blender im Hintergrund...")
    print("   (Fortschritt wird in Echtzeit angezeigt)\n")
    cmd = [BLENDER_PATH, "--background", "--python", BLENDER_SCRIPT_PATH]

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="ignore"
    )

    print("================= BLENDER OUTPUT START =================")
    for line in process.stdout:
        print(line, end="", flush=True)
    process.wait()
    print("\n================== BLENDER OUTPUT END ==================\n")

    if process.returncode != 0:
        print(f"WARNUNG: Blender beendete sich mit Exit-Code {process.returncode}")
    else:
        print("-> Blender-Prozess erfolgreich abgeschlossen!")

def stitch_spritesheets():
    print("-> Klebe Einzelbilder zu Spritesheets zusammen...")

    for anim in ANIMATIONS:
        search_pattern = os.path.join(TEMP_DIR, f"{anim}_dir0_frame*.png")
        frame_files = sorted(glob.glob(search_pattern))
        frame_count = len(frame_files)

        if frame_count == 0:
            print(f"   [!] Keine Frames für Animation '{anim}' gefunden. Überspringe...")
            continue

        print(f"   Erstelle Sheet für '{anim}' ({frame_count} Frames x 8 Richtungen)...")

        sheet_width  = FRAME_WIDTH  * frame_count
        sheet_height = FRAME_HEIGHT * 8
        spritesheet  = Image.new("RGBA", (sheet_width, sheet_height), (0, 0, 0, 0))

        for dir_index in range(8):
            for frame_index in range(frame_count):
                filename = f"{anim}_dir{dir_index}_frame{frame_index:04d}.png"
                filepath = os.path.join(TEMP_DIR, filename)

                if os.path.exists(filepath):
                    with Image.open(filepath) as img:
                        x_pos = frame_index * FRAME_WIDTH
                        y_pos = dir_index   * FRAME_HEIGHT
                        spritesheet.paste(img, (x_pos, y_pos))
                else:
                    print(f"   [!] Fehlender Frame: {filename}")

        final_png_path = os.path.join(OUTPUT_DIR, f"{anim}_spritesheet.png")
        spritesheet.save(final_png_path)
        print(f"   [+] Gespeichert: {final_png_path}")

def cleanup():
    print("-> Räume temporäre Dateien auf...")
    if os.path.exists(TEMP_DIR):
        shutil.rmtree(TEMP_DIR)
    if os.path.exists(BLENDER_SCRIPT_PATH):
        os.remove(BLENDER_SCRIPT_PATH)
    print("-> Fertig!")

if __name__ == "__main__":
    print("=== AUTOMATISCHER PIXELART SPRITESHEET GENERATOR ===")
    check_paths()
    generate_blender_script()
    run_blender()
    stitch_spritesheets()
    cleanup()
    print("=== PROZESS ERFOLGREICH BEENDET ===")
