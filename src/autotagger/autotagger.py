import argparse
import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import importlib
from pathlib import Path

import onnxruntime as rt
import numpy as np
from PIL import Image, ImageOps, ImageStat
import pandas as pd
import cv2

try:
    extractMetadata = importlib.import_module("hachoir.metadata").extractMetadata
    createParser = importlib.import_module("hachoir.parser").createParser
except Exception:  # pragma: no cover - optional dependency fallback
    extractMetadata = None
    createParser = None

# --- CONFIGURACIÓN ---
MODEL_PATH = "models/model.onnx"
MODEL_PATH_FALLBACKS = (
    "assets/models/model.onnx",
    "models/model.onnx",
)
CSV_PATH = "models/selected_tags.csv"
CSV_PATH_FALLBACKS = (
    "assets/selected_tags.csv",
    "models/assets/selected_tags.csv",
    "models/selected_tags.csv",
)
UMBRAL_CONFIANZA = 0.55  # Threshold de confianza a partir del cual damos un tag por bueno
PREFERRED_EXECUTION_PROVIDERS = (
    "DmlExecutionProvider",
    "CPUExecutionProvider",
)

# --- VÍDEO ---
VIDEO_EXTENSIONS = {
    ".mp4", ".ts", ".mov", ".mkv", ".avi", ".webm", ".flv", ".wmv", ".m4v", ".mpeg", ".mpg",
}
AUTO_FRAME_INTERVAL = "auto"
DEFAULT_SCENE_THRESHOLD = 0.35
DEFAULT_AUTO_CANDIDATE_INTERVAL = 3.0
MAX_AUTO_CANDIDATE_FRAMES = 1200
AUTO_FORCE_MAX_GAP_SECONDS = 120.0
DEFAULT_FFMPEG_START_TIMEOUT_SECONDS = 20.0


def get_preferred_onnx_providers():
    """Devuelve los providers de ONNX Runtime por orden de preferencia."""
    available_providers = rt.get_available_providers()
    providers = [
        provider for provider in PREFERRED_EXECUTION_PROVIDERS
        if provider in available_providers
    ]

    if not providers:
        return ["CPUExecutionProvider"]

    if "CPUExecutionProvider" not in providers:
        providers.append("CPUExecutionProvider")

    return providers


def _get_runtime_base_dirs():
    """Devuelve los directorios base donde buscar recursos en ejecución."""
    base_dirs = []

    if getattr(sys, "frozen", False):
        base_dirs.append(Path(sys.executable).resolve().parent)
        meipass_dir = getattr(sys, "_MEIPASS", None)
        if meipass_dir:
            base_dirs.append(Path(meipass_dir))

    base_dirs.append(Path(__file__).resolve().parent)

    unique_dirs = []
    seen_dirs = set()
    for base_dir in base_dirs:
        normalized = os.fspath(base_dir)
        if normalized in seen_dirs:
            continue
        seen_dirs.add(normalized)
        unique_dirs.append(base_dir)
    return unique_dirs


def resolve_resource_path(resource_path):
    """Resuelve rutas relativas para script normal o binario PyInstaller."""
    resource = Path(resource_path)
    if resource.is_absolute():
        return resource

    for base_dir in _get_runtime_base_dirs():
        candidates = [base_dir / resource.name, base_dir / resource]
        for candidate in candidates:
            if candidate.exists():
                return candidate

    return _get_runtime_base_dirs()[0] / resource


def resolve_first_resource_path(*resource_paths):
    """Devuelve la primera ruta de recurso existente entre varias candidatas."""
    missing_candidates = []
    for resource_path in resource_paths:
        resolved_path = resolve_resource_path(resource_path)
        if resolved_path.exists():
            return resolved_path
        missing_candidates.append(str(resolved_path))

    raise FileNotFoundError(
        "No se encontró ninguno de los recursos esperados: " + ", ".join(missing_candidates)
    )


def resolve_model_path(model_path):
    """Prioriza el modelo empaquetado y luego alternativas externas."""
    candidate_paths = []
    if model_path:
        candidate_paths.append(model_path)

    for fallback_path in MODEL_PATH_FALLBACKS:
        if fallback_path not in candidate_paths:
            candidate_paths.append(fallback_path)

    if "assets/models/model.onnx" in candidate_paths:
        candidate_paths.remove("assets/models/model.onnx")
    candidate_paths.insert(0, "assets/models/model.onnx")

    return resolve_first_resource_path(*candidate_paths)

# --- DICCIONARIO DE MAPEO ---
# Clave: TU etiqueta personalizada 
# Valor: Lista de etiquetas que activarán las etiquetas finales
MAPEO_PERSONALIZADO = {
    # Etiquetas de color de pelo
    "blonde": ["blonde_hair"],
    "black_hair": ["black_hair"],
    "brown_hair": ["brown_hair"],
    "redhead": ["red_hair", "orange_hair"],
    # Etiquetas de pechos
    "large_breasts": ["large_breasts", "huge_breasts", "gigantic_breasts"],
    "medium_breasts": ["medium_breasts"],
    "small_breasts": ["small_breasts"],
    "saggy_breasts": ["hanging_breasts", "sagging_breasts"],
    "perky_breasts": ["perky_breasts", "pointy_breasts"],
    # Etiquetas de pene
    "big_cock": ["large_penis","huge_penis"],
    "small_cock": ["small_penis"],
    # Etiquetas de mods cuerpo
    "tattoo": ["tattoo", "arm_tattoo", "chest_tattoo", "pubic_tattoo", "facial_tattoo","breast_tattoo", "stomach_tattoo", "shoulder_tattoo", "back_tattoo", "leg_tattoo"],
    "kissing": ["kiss", "kissing_cheek", "kissing_forehead", "kissing_neck"],
    # Skin color
    "dark_skin": ["dark_skin"],

    # Fisonomía corporal
    "curvy": ["curvy", "wide_hips", "thick_thighs"],
    "athletic": ["muscular", "muscular_female", "abs", "toned"],
    "petite": ["petite", "skinny", "narrow_waist", "thigh_gap"],
    "chubby": ["plump", "fat", "big_belly", "belly", "fat_rolls"],

    # Tipos de culo
    "big_ass": ["huge_ass", "ass_focus", "ass_visible_through_thighs"],
    "flat_ass": ["flat_ass"],

    # Sexual actions - Oral
    "deepthroat": ["deepthroat"],
    "blowjob": ["fellatio", "deepthroat", "irrumatio"],
    "cunnilingus": ["cunnilingus"],
    "anilingus": ["anilingus"],
    "69": ["69"],
    "licking": ["licking"],
    "saliva": ["saliva", "saliva_drip", "saliva_trail"],
    "tongue": ["tongue", "tongue_out"],

    # Sexual actions - Penetration
    "anal": ["anal", "anal_object_insertion", "anal_beads", "anal_fingering"],
    "vaginal": ["vaginal", "vaginal_object_insertion"],
    "double_penetration": ["double_penetration"],
    "triple_penetration": ["triple_penetration"],

    # Sexual actions - Manual/Other
    "handjob": ["handjob", "double_handjob", "nursing_handjob"],
    "footjob": ["footjob", "two-footed_footjob"],
    "fingering": ["fingering", "anal_fingering"],
    "masturbation": ["masturbation", "female_masturbation", "male_masturbation"],

    # Sexual actions - Group
    "threesome": ["threesome", "ffm_threesome", "mmf_threesome", "fff_threesome", "mmm_threesome", "spitroast"],
    "gangbang": ["gangbang", "group_sex", "orgy", "bukkake"],

    # Positions
    "doggystyle": ["doggystyle", "sex_from_behind"],
    "missionary": ["missionary", "mating_press"],
    "cowgirl": ["cowgirl_position", "reverse_cowgirl_position", "squatting_cowgirl_position", "riding"],
    "standing_sex": ["standing_sex", "against_wall", "against_glass", "suspended_congress", "reverse_suspended_congress"],
    "spread_legs": ["legs_up", "spread_legs"],
    "face_sitting": ["sitting_on_face", "sitting_on_person"],
    "kneeling": ["kneeling"],

    # Expressions / Reactions
    "ahegao": ["ahegao"],
    "orgasm": ["orgasm", "female_orgasm"],

    # Cum-related
    "creampie": ["cum_in_pussy", "cum_in_ass", "cum_overflow", "internal_cumshot", "after_vaginal", "after_anal"],
    "facial_cumshot": ["facial", "cum_on_body", "cum_on_breasts", "cum_on_hair", "cum_on_stomach",
                        "cum_on_tongue", "cum_in_mouth", "bukkake"],

    # BDSM / Kink
    "bondage": ["bondage", "bound", "bound_wrists", "bound_arms", "bound_legs", "hogtie",
                 "breast_bondage", "restrained", "suspension"],
    "collar_and_leash": ["collar", "leash", "spiked_collar", "chain_leash"],
    "blindfold": ["blindfold"],
    "gag": ["gag", "ball_gag", "ring_gag", "bit_gag", "tape_gag"],
    "spanking": ["spanked", "spanking"],
    "nipple_clamps": ["nipple_clamps", "nipple_chain"],
    "shibari": ["shibari", "rope", "red_rope", "crotch_rope"],
    "feet": ["feet", "soaking_feet", "feet_up", "cum_on_feet", "dirty_feet", "barefoot"],
    "high_heels": ["high_heels", "stiletto_heels"],
    "object_insertion": ["object_insertion", "anal_object_insertion", "vaginal_object_insertion"],
    "squirting": ["female_ejaculation"],
    "mask": ["mask", "mask_on_head", "gas_mask"],

    # Fetish clothing / Accessories
    "piercing": ["nipple_piercing", "pussy_piercing", "clitoris_piercing", "navel_piercing", 
                "barbell_piercing", "tongue_piercing", "nose_piercing", "lip_piercing", 
                "eyebrow_piercing", "multiple_piercings"],
    "nipple_piercing": ["nipple_piercing", "pierced_nipples", "pierced_breasts", "nipple_rings"],
    "pussy_piercing": ["pussy_piercing", "clitoris_piercing"],

    "cat_ears": ["cat_ears", "cat_tail", "cat_lingerie", "cat_girl"],
    "cosplay": ["cosplay"],
    "glasses": ["glasses", "sunglasses", "eyepatch"],
    "bunny_outfit": ["rabbit_ears", "playboy_bunny", "reverse_bunnysuit"],
    "animal_ears": ["animal_ears", "fox_ears", "dog_ears", "wolf_ears", "fake_animal_ears"],
    "latex_outfit": ["latex", "latex_bodysuit", "latex_gloves", "latex_legwear"],
    "leather_outfit": ["leather", "leather_jacket", "leather_gloves", "leather_boots", "leather_pants"],
    "stockings": ["thighhighs", "black_thighhighs", "white_thighhighs", "striped_thighhighs"],
    "garter_belt": ["garter_straps", "garter_belt"],
    "fishnets": ["fishnets", "fishnet_pantyhose", "fishnet_thighhighs", "fishnet_bodysuit"],
    "pantyhose": ["pantyhose", "black_pantyhose", "crotchless_pantyhose"],
    "bodystocking": ["bodystocking", "torn_bodystocking"],
    "corset": ["corset", "black_corset"],
    "choker": ["choker", "black_choker", "o-ring_choker"],
    "leotard": ["leotard", "highleg_leotard", "thong_leotard", "see-through_leotard"],
    "bodysuit": ["bodysuit", "black_bodysuit", "latex_bodysuit", "fishnet_bodysuit"],
    "lingerie_see_through": ["see-through", "see-through_dress", "see-through_shirt", "see-through_legwear"],
    "crotchless": ["crotchless", "crotchless_panties", "crotchless_pantyhose", "crotch_cutout"],
    "naked_apron": ["naked_apron"],
    "virgin_killer": ["virgin_killer_sweater", "virgin_killer_outfit"],
    "maid_outfit": ["maid", "maid_headdress", "maid_apron", "enmaided"],
    "nurse_outfit": ["nurse", "nurse_cap"],
    "schoolgirl": ["school_uniform", "serafuku"],
    "nun": ["nun", "traditional_nun"],
    "china_dress": ["china_dress"],
    "kimono": ["kimono", "open_kimono"],
    "harness": ["harness", "chest_harness"],
    "micro_bikini": ["micro_bikini", "string_bikini", "slingshot_swimsuit"],
    "pasties": ["pasties", "heart_pasties", "bandaids_on_nipples", "tape_on_nipples", "nippleless_clothes"],
    "elf_ears": ["pointy_ears"],
    "gothic": ["gothic_lolita", "goth_fashion"],

    # Toys
    "dildo": ["dildo", "huge_dildo", "double_dildo", "dildo_riding"],
    "vibrator": ["vibrator", "egg_vibrator", "remote_control_vibrator"],
    "strapon": ["strap-on"],
    "magic_wand": ["wand", "hitachi_magic_wand", "holding_wand"],

    # Condom
    "condom": ["condom", "used_condom", "condom_on_penis"],

    # Situational
    "exhibitionism": ["exhibitionism", "public_indecency", "public_nudity", "caught"],
    "pregnant": ["pregnant", "impregnation"],
    "smoking": ["smoking"],

    # Etiquetas de lugar
    "outdoors": ["outdoors", "sky", "day", "nature", "cityscape", "car_interior"],
    "car": ["car_interior"],
    "beach": ["beach", "ocean"],
    "bathroom": ["bathroom", "shower_(place)", "bathing", "bathtub"],
    "shower": ["showering", "shower_(place)"],
    "kitchen": ["kitchen"],
}

# --- REGLAS ESPECIALES ---
# Reglas con lógica más compleja que un simple OR de tags.
# Cada regla es un dict con:
#   "any": lista de tags donde basta que UNO supere el umbral (OR)
#   "all": lista de tags donde TODOS deben superar el umbral (AND)
# Se cumple la regla si al menos un bloque "any" o "all" se satisface.
REGLAS_ESPECIALES = {
    "couple": [
        {"all": ["1girl", "1boy"]},
        {"any": ["group_sex"]},
    ],
}

# --- GRUPOS MUTUAMENTE EXCLUYENTES (sin "couple") ---
# Si "couple" no está presente, solo puede quedar una etiqueta de cada grupo.
# Se conserva la que aparezca en más frames.
GRUPOS_EXCLUYENTES = [
    {"blonde", "black_hair", "brown_hair", "redhead"},
    {"large_breasts", "medium_breasts", "small_breasts"},
    {"big_cock", "small_cock"},
    {"curvy", "athletic", "petite", "chubby"},
    {"big_ass", "flat_ass"},
]

# Grupos de atributos femeninos que solo permiten co-ocurrencia
# cuando están presentes TANTO "couple" COMO "lesbian".
GRUPOS_REQUIERE_LESBIAN = {
    frozenset({"large_breasts", "medium_breasts", "small_breasts"}),
}


def sanitizar_tags_video(all_tags, frames_result):
    """Elimina incoherencias en las etiquetas agregadas del vídeo.

    - 'couple' se descarta si no aparece en al menos el 40% de los frames.
    - Si no hay etiqueta 'couple', dentro de cada grupo excluyente solo se
      mantiene la etiqueta que aparece en más frames.
    """
    # Contar apariciones de cada tag en los frames
    conteo = {}
    for frame in frames_result:
        for tag in frame["tags"]:
            conteo[tag] = conteo.get(tag, 0) + 1

    # Filtrar 'couple' si no alcanza el 40 % de los frames
    total_frames = len(frames_result)
    if "couple" in all_tags and conteo.get("couple", 0) < total_frames * 0.4:
        all_tags = [t for t in all_tags if t != "couple"]

    es_couple = "couple" in all_tags
    es_lesbian = "lesbian" in all_tags

    tags_a_eliminar = set()
    for grupo in GRUPOS_EXCLUYENTES:
        if es_couple:
            if frozenset(grupo) not in GRUPOS_REQUIERE_LESBIAN or es_lesbian:
                continue
        presentes = [t for t in grupo if t in all_tags]
        if len(presentes) > 1:
            # Conservar la de mayor conteo, desempate por orden de aparición
            ganadora = max(presentes, key=lambda t: conteo.get(t, 0))
            for t in presentes:
                if t != ganadora:
                    tags_a_eliminar.add(t)

    if not tags_a_eliminar:
        return all_tags

    return [t for t in all_tags if t not in tags_a_eliminar]


def sanitizar_tags_frame(tags_finales, confianzas):
    """Elimina incoherencias en un frame individual.

    Si 'couple' no está presente, dentro de cada grupo excluyente solo se
    mantiene la etiqueta con mayor confianza.

    *tags_finales*: lista de etiquetas del frame.
    *confianzas*: dict {etiqueta: prob_máxima} del mismo frame.
    """
    es_couple = "couple" in tags_finales
    es_lesbian = "lesbian" in tags_finales

    tags_a_eliminar = set()
    for grupo in GRUPOS_EXCLUYENTES:
        if es_couple:
            if frozenset(grupo) not in GRUPOS_REQUIERE_LESBIAN or es_lesbian:
                continue
        presentes = [t for t in grupo if t in tags_finales]
        if len(presentes) > 1:
            ganadora = max(presentes, key=lambda t: confianzas.get(t, 0.0))
            for t in presentes:
                if t != ganadora:
                    tags_a_eliminar.add(t)

    if not tags_a_eliminar:
        return tags_finales

    return [t for t in tags_finales if t not in tags_a_eliminar]


def preparar_imagen(image_path, target_size):
    """Prepara la imagen para el formato que espera el modelo ONNX (NHWC, BGR)"""
    img = Image.open(image_path).convert('RGB')
    
    # Rellenar (Pad) para hacerla cuadrada sin deformar
    max_dim = max(img.size)
    pad_img = Image.new('RGB', (max_dim, max_dim), (255, 255, 255))
    pad_img.paste(img, ((max_dim - img.size[0]) // 2, (max_dim - img.size[1]) // 2))
    
    # Redimensionar al tamaño del modelo (ej. 448x448)
    pad_img = pad_img.resize((target_size, target_size), Image.Resampling.LANCZOS)
    
    # Convertir a numpy, formato BGR (estándar OpenCV/WD Tagger) y Float32
    img_array = np.array(pad_img)
    img_array = img_array[:, :, ::-1] # RGB a BGR
    img_array = np.expand_dims(img_array, axis=0).astype(np.float32)
    
    return img_array


def preparar_imagen_array(image_array, target_size):
    """Prepara una imagen BGR ya cargada en memoria para el modelo ONNX."""
    if image_array is None or getattr(image_array, "size", 0) == 0:
        raise ValueError("La imagen en memoria está vacía.")

    height, width = image_array.shape[:2]
    max_dim = max(height, width)
    pad_img = np.full((max_dim, max_dim, 3), 255, dtype=np.uint8)
    offset_y = (max_dim - height) // 2
    offset_x = (max_dim - width) // 2
    pad_img[offset_y:offset_y + height, offset_x:offset_x + width] = image_array

    if max_dim != target_size:
        pad_img = cv2.resize(pad_img, (target_size, target_size), interpolation=cv2.INTER_LANCZOS4)

    return np.expand_dims(pad_img, axis=0).astype(np.float32)

def cargar_etiquetas_danbooru(csv_path):
    """Carga los nombres de los tags en el orden exacto de salida del modelo"""
    df = pd.read_csv(csv_path)
    nombres_tags = df['name'].tolist()
    return nombres_tags


# ---------------------------------------------------------------------------
# Funciones de soporte para vídeo
# ---------------------------------------------------------------------------

def is_video_file(path: Path) -> bool:
    return path.suffix.lower() in VIDEO_EXTENSIONS


def _get_video_duration_hachoir(video_path: Path):
    if createParser is None or extractMetadata is None:
        return None
    try:
        parser = createParser(str(video_path))
        if parser is None:
            return None
        with parser:
            metadata = extractMetadata(parser)
            if metadata is None:
                return None
            duration = metadata.get("duration")
            if duration is None:
                return None
            total_seconds = float(getattr(duration, "total_seconds", lambda: 0.0)())
            return total_seconds if total_seconds > 0 else None
    except Exception:
        return None


def _get_video_duration_opencv(video_path: Path):
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        return None
    try:
        frame_count = cap.get(cv2.CAP_PROP_FRAME_COUNT)
        fps = cap.get(cv2.CAP_PROP_FPS)
        if fps > 0 and frame_count > 0:
            return frame_count / fps
        return None
    finally:
        cap.release()


def get_video_duration(video_path: Path):
    duration_seconds = _get_video_duration_hachoir(video_path)
    if duration_seconds is not None:
        return duration_seconds
    return _get_video_duration_opencv(video_path)


def ensure_ffmpeg_available(ffmpeg_bin: str) -> str:
    resolved = shutil.which(ffmpeg_bin)
    if resolved:
        return resolved
    explicit = Path(ffmpeg_bin)
    if explicit.exists():
        return str(explicit)
    print(f"Error: ffmpeg no encontrado: {ffmpeg_bin}", file=sys.stderr)
    print("Instala ffmpeg y asegúrate de que esté en PATH, o usa --ffmpeg-bin.", file=sys.stderr)
    sys.exit(1)


def parse_frame_interval(value: str):
    stripped = str(value).strip().lower()
    if stripped == AUTO_FRAME_INTERVAL:
        return AUTO_FRAME_INTERVAL
    try:
        interval_seconds = float(stripped)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--frame-interval debe ser un número positivo o 'auto'.") from exc
    if interval_seconds <= 0:
        raise argparse.ArgumentTypeError("--frame-interval debe ser mayor que cero.")
    return interval_seconds


def _run_ffmpeg_interruptible(
    command: list,
    stop_event: threading.Event | None = None,
    *,
    startup_timeout_seconds: float | None = None,
    progress_glob: str | None = None,
    progress_dir: Path | None = None,
    operation: str = "ffmpeg",
) -> None:
    """Run an ffmpeg command, killing it if stop_event is set or if it never starts producing frames."""
    proc = subprocess.Popen(
        command,
        stderr=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        text=True,
        errors="replace",
    )
    started_at = time.monotonic()
    extraction_started = False
    last_frame_count = 0
    stderr_lines: list[str] = []
    stderr_done = threading.Event()

    def _stderr_reader() -> None:
        try:
            if proc.stderr is None:
                return
            for raw in proc.stderr:
                line = (raw or "").strip()
                if not line:
                    continue
                stderr_lines.append(line)
                if len(stderr_lines) > 200:
                    del stderr_lines[:100]
        finally:
            stderr_done.set()

    stderr_thread = threading.Thread(target=_stderr_reader, daemon=True)
    stderr_thread.start()

    try:
        while True:
            try:
                proc.wait(timeout=0.25)
                break
            except subprocess.TimeoutExpired:
                if stop_event is not None and stop_event.is_set():
                    proc.kill()
                    proc.wait()
                    raise InterruptedError("Auto-tag cancelled")

                if progress_dir is not None and progress_glob:
                    frame_count = sum(1 for _ in progress_dir.glob(progress_glob))
                    if frame_count > last_frame_count:
                        last_frame_count = frame_count
                        extraction_started = True

                if (
                    not extraction_started
                    and startup_timeout_seconds is not None
                    and time.monotonic() - started_at >= startup_timeout_seconds
                ):
                    proc.kill()
                    proc.wait()
                    raise TimeoutError(
                        f"ffmpeg did not start {operation} within {startup_timeout_seconds:.0f}s"
                    )
    except InterruptedError:
        raise
    except Exception:
        try:
            proc.kill()
            proc.wait()
        except Exception:
            pass
        raise
    if proc.returncode != 0:
        stderr_done.wait(timeout=1.0)
        stderr_out = ""
        try:
            if stderr_lines:
                stderr_out = "\n".join(stderr_lines)
            elif proc.stderr:
                stderr_out = proc.stderr.read() or ""
        except Exception:
            pass
        raise subprocess.CalledProcessError(proc.returncode, command, stderr=stderr_out)
    stderr_done.wait(timeout=1.0)


def _ffmpeg_input_args(video_path: Path) -> list[str]:
    """Return safe ffmpeg args for opening a video path, handling paths that
    start with '-' which ffmpeg would treat as a flag."""
    path_str = str(video_path)
    # If the path string starts with '-', prefix with './' won't help on
    # Windows.  Instead, use the '--' end-of-options sentinel then the path.
    # ffmpeg supports '--' as end of global options before -i.
    if path_str.startswith("-"):
        return ["-i", "--", path_str]
    return ["-i", path_str]


def extract_video_frames_fixed(video_path: Path, output_dir: Path, interval_seconds: float, ffmpeg_bin: str,
                               stop_event: threading.Event | None = None):
    ffmpeg = ensure_ffmpeg_available(ffmpeg_bin)
    output_pattern = output_dir / "frame_%06d.jpg"
    command = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        *_ffmpeg_input_args(video_path),
        "-vf", f"fps=1/{interval_seconds}",
        "-q:v", "2",
        str(output_pattern),
    ]
    _run_ffmpeg_interruptible(
        command,
        stop_event,
        startup_timeout_seconds=DEFAULT_FFMPEG_START_TIMEOUT_SECONDS,
        progress_dir=output_dir,
        progress_glob="frame_*.jpg",
        operation="extracting fixed-interval frames",
    )
    frames = sorted(output_dir.glob("frame_*.jpg"))
    if not frames:
        raise RuntimeError("ffmpeg no extrajo ningún frame del vídeo.")
    frame_entries = []
    for index, frame_path in enumerate(frames, start=1):
        frame_entries.append({
            "path": frame_path,
            "timestamp_seconds": max(0.0, (index - 1) * interval_seconds),
        })
    return frame_entries, None


def compute_auto_candidate_interval(duration_seconds) -> float:
    if duration_seconds is None or duration_seconds <= 0:
        return DEFAULT_AUTO_CANDIDATE_INTERVAL
    return max(DEFAULT_AUTO_CANDIDATE_INTERVAL, duration_seconds / MAX_AUTO_CANDIDATE_FRAMES)


def fingerprint_image(image_path: Path):
    with Image.open(image_path) as image:
        grayscale = ImageOps.grayscale(image).resize((32, 32), Image.Resampling.BILINEAR)
        pixels = list(grayscale.getdata())
        mean_value = sum(pixels) / len(pixels)
        bits = tuple(pixel >= mean_value for pixel in pixels)
        stats = ImageStat.Stat(grayscale)
        return {
            "bits": bits,
            "mean": stats.mean[0],
            "stddev": stats.stddev[0],
        }


def fingerprint_array(image_array):
    grayscale = cv2.cvtColor(image_array, cv2.COLOR_BGR2GRAY)
    grayscale = cv2.resize(grayscale, (32, 32), interpolation=cv2.INTER_AREA)
    pixels = grayscale.astype(np.float32).reshape(-1)
    mean_value = float(np.mean(pixels))
    return {
        "bits": tuple(bool(pixel >= mean_value) for pixel in pixels),
        "mean": mean_value,
        "stddev": float(np.std(pixels)),
    }


def frame_change_score(previous_fingerprint, current_fingerprint):
    bit_count = len(previous_fingerprint["bits"])
    hamming_ratio = sum(
        prev_bit != curr_bit
        for prev_bit, curr_bit in zip(previous_fingerprint["bits"], current_fingerprint["bits"])
    ) / bit_count
    mean_delta = abs(previous_fingerprint["mean"] - current_fingerprint["mean"]) / 255.0
    stddev_delta = abs(previous_fingerprint["stddev"] - current_fingerprint["stddev"]) / 128.0
    return max(hamming_ratio, mean_delta * 0.7 + stddev_delta * 0.3)


def _build_ffmpeg_rawvideo_filter(interval_seconds: float, target_size: int) -> str:
    return ",".join([
        f"fps=1/{interval_seconds}",
        f"scale={target_size}:{target_size}:force_original_aspect_ratio=decrease:flags=lanczos",
        f"pad={target_size}:{target_size}:(ow-iw)/2:(oh-ih)/2:color=white",
        "format=bgr24",
    ])


def _iter_ffmpeg_rawvideo_frames(
    video_path: Path,
    interval_seconds: float,
    ffmpeg_bin: str,
    target_size: int,
    stop_event: threading.Event | None = None,
    *,
    startup_timeout_seconds: float | None = None,
    operation: str = "streaming video frames",
):
    ffmpeg = ensure_ffmpeg_available(ffmpeg_bin)
    frame_bytes = target_size * target_size * 3
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-hwaccel",
        "auto",
        *_ffmpeg_input_args(video_path),
        "-an",
        "-sn",
        "-dn",
        "-vf",
        _build_ffmpeg_rawvideo_filter(interval_seconds, target_size),
        "-pix_fmt",
        "bgr24",
        "-f",
        "rawvideo",
        "pipe:1",
    ]
    proc = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        bufsize=frame_bytes * 2,
    )
    started_at = time.monotonic()
    extraction_started = False
    stderr_lines: list[str] = []
    stderr_done = threading.Event()
    stdout_done = threading.Event()
    frame_queue: queue.Queue[np.ndarray] = queue.Queue(maxsize=8)
    stdout_error: list[BaseException] = []

    def _stderr_reader() -> None:
        try:
            if proc.stderr is None:
                return
            for raw in proc.stderr:
                line = raw.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                stderr_lines.append(line)
                if len(stderr_lines) > 200:
                    del stderr_lines[:100]
        finally:
            stderr_done.set()

    def _stdout_reader() -> None:
        try:
            if proc.stdout is None:
                return
            while True:
                raw = proc.stdout.read(frame_bytes)
                if not raw:
                    break
                if len(raw) != frame_bytes:
                    raise RuntimeError(
                        f"ffmpeg devolvió un frame incompleto ({len(raw)} de {frame_bytes} bytes)."
                    )
                frame = np.frombuffer(raw, dtype=np.uint8).reshape((target_size, target_size, 3)).copy()
                frame_queue.put(frame)
        except BaseException as exc:  # pragma: no cover - defensive guard for streaming threads
            stdout_error.append(exc)
        finally:
            stdout_done.set()

    stderr_thread = threading.Thread(target=_stderr_reader, daemon=True)
    stdout_thread = threading.Thread(target=_stdout_reader, daemon=True)
    stderr_thread.start()
    stdout_thread.start()

    frame_index = 0

    try:
        while True:
            if stop_event is not None and stop_event.is_set():
                proc.kill()
                proc.wait()
                raise InterruptedError("Auto-tag cancelled")

            if stdout_error:
                raise stdout_error[0]

            try:
                frame = frame_queue.get(timeout=0.25)
            except queue.Empty:
                if stdout_done.is_set():
                    break
                if (
                    not extraction_started
                    and startup_timeout_seconds is not None
                    and time.monotonic() - started_at >= startup_timeout_seconds
                ):
                    proc.kill()
                    proc.wait()
                    raise TimeoutError(
                        f"ffmpeg did not start {operation} within {startup_timeout_seconds:.0f}s"
                    )
                continue

            extraction_started = True
            frame_index += 1
            yield frame_index, frame

        proc.wait()
        if proc.returncode != 0:
            stderr_done.wait(timeout=1.0)
            raise subprocess.CalledProcessError(proc.returncode, command, stderr="\n".join(stderr_lines))
    finally:
        if proc.poll() is None:
            try:
                proc.kill()
                proc.wait(timeout=1.0)
            except Exception:
                pass
        stdout_done.wait(timeout=1.0)
        stderr_done.wait(timeout=1.0)


def extract_video_frames_auto(
    video_path: Path, output_dir: Path, scene_threshold: float, ffmpeg_bin: str, duration_seconds,
    stop_event: threading.Event | None = None,
):
    ffmpeg = ensure_ffmpeg_available(ffmpeg_bin)
    candidate_interval = compute_auto_candidate_interval(duration_seconds)
    candidates_dir = output_dir / "candidates"
    selected_dir = output_dir / "selected"
    candidates_dir.mkdir(parents=True, exist_ok=True)
    selected_dir.mkdir(parents=True, exist_ok=True)

    output_pattern = candidates_dir / "frame_%06d.jpg"
    command = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        *_ffmpeg_input_args(video_path),
        "-vf", f"fps=1/{candidate_interval}",
        "-q:v", "2",
        str(output_pattern),
    ]
    print(
        f"[Video] Modo auto con detección de escena: muestreando frames candidatos cada {candidate_interval:.2f}s (timeout inicio={DEFAULT_FFMPEG_START_TIMEOUT_SECONDS:.0f}s)",
        file=sys.stderr,
    )
    _run_ffmpeg_interruptible(
        command,
        stop_event,
        startup_timeout_seconds=DEFAULT_FFMPEG_START_TIMEOUT_SECONDS,
        progress_dir=candidates_dir,
        progress_glob="frame_*.jpg",
        operation="extracting candidate frames",
    )
    candidate_frames = sorted(candidates_dir.glob("frame_*.jpg"))
    if not candidate_frames:
        raise RuntimeError("ffmpeg no extrajo ningún frame candidato del vídeo.")

    #print(
    #    f"[Video] Modo auto: {len(candidate_frames)} frames candidatos muestreados cada {candidate_interval:.2f}s",
    #    file=sys.stderr,
    #)

    frame_entries = []
    previous_fingerprint = None
    previous_selected_timestamp = None
    for index, frame_path in enumerate(candidate_frames, start=1):
        timestamp_seconds = max(0.0, (index - 1) * candidate_interval)
        current_fingerprint = fingerprint_image(frame_path)
        should_select = previous_fingerprint is None
        if not should_select:
            change_score = frame_change_score(previous_fingerprint, current_fingerprint)
            forced_by_gap = (
                previous_selected_timestamp is not None
                and timestamp_seconds - previous_selected_timestamp >= AUTO_FORCE_MAX_GAP_SECONDS
            )
            should_select = change_score >= scene_threshold or forced_by_gap
        if not should_select:
            continue

        selected_path = selected_dir / f"frame_{len(frame_entries) + 1:06d}.jpg"
        shutil.copy2(frame_path, selected_path)
        frame_entries.append({
            "path": selected_path,
            "timestamp_seconds": round(timestamp_seconds, 3),
        })
        previous_fingerprint = current_fingerprint
        previous_selected_timestamp = timestamp_seconds

    if not frame_entries:
        selected_path = selected_dir / "frame_000001.jpg"
        shutil.copy2(candidate_frames[0], selected_path)
        frame_entries.append({"path": selected_path, "timestamp_seconds": 0.0})

    print(
        f"[Video] Modo auto: {len(frame_entries)} frames seleccionados para etiquetar "
        f"(de {len(candidate_frames)} candidatos, umbral={scene_threshold:.2f})",
        file=sys.stderr,
    )

    return frame_entries, len(candidate_frames)


def extract_video_frames(video_path, output_dir, frame_interval, ffmpeg_bin, scene_threshold, duration_seconds,
                         stop_event=None):
    if frame_interval == AUTO_FRAME_INTERVAL:
        return extract_video_frames_auto(video_path, output_dir, scene_threshold, ffmpeg_bin, duration_seconds,
                                         stop_event=stop_event)
    entries, _ = extract_video_frames_fixed(video_path, output_dir, float(frame_interval), ffmpeg_bin,
                                            stop_event=stop_event)
    return entries, None


# ---------------------------------------------------------------------------
# Lógica de etiquetado
# ---------------------------------------------------------------------------

def _evaluar_reglas_especiales(resultados_danbooru, umbral):
    """Evalúa REGLAS_ESPECIALES y devuelve las etiquetas activadas."""
    tags = []
    for mi_tag, bloques in REGLAS_ESPECIALES.items():
        for bloque in bloques:
            if "all" in bloque:
                if all(resultados_danbooru.get(t, 0.0) >= umbral for t in bloque["all"]):
                    tags.append(mi_tag)
                    break
            if "any" in bloque:
                if any(resultados_danbooru.get(t, 0.0) >= umbral for t in bloque["any"]):
                    tags.append(mi_tag)
                    break
    return tags


# ---------------------------------------------------------------------------
# Clase principal — API reutilizable
# ---------------------------------------------------------------------------

class NSFWAutoTagger:
    """Etiquetador NSFW basado en modelos ONNX tipo WD-Tagger.

    El modelo se carga una sola vez al crear la instancia (o al llamar a
    ``set_model``).  Uso típico::

        tagger = NSFWAutoTagger()
        tags = tagger.tag_image("foto.jpg")
        tags = tagger.tag_video("video.mp4")
    """

    def __init__(self, model_path=MODEL_PATH, csv_path=CSV_PATH,
                 umbral_confianza=UMBRAL_CONFIANZA):
        self.umbral_confianza = umbral_confianza
        self._load_model(model_path, csv_path)

    # -- Gestión del modelo --------------------------------------------------

    def _load_model(self, model_path, csv_path):
        resolved_model_path = resolve_model_path(model_path)
        resolved_csv_path = resolve_first_resource_path(csv_path, *CSV_PATH_FALLBACKS)
        self.model_path = str(resolved_model_path)
        self.csv_path = str(resolved_csv_path)
        self.available_providers = rt.get_available_providers()
        self.requested_providers = get_preferred_onnx_providers()
        self.session = rt.InferenceSession(
            self.model_path, providers=self.requested_providers,
        )
        self.active_providers = self.session.get_providers()
        self.active_provider = self.active_providers[0] if self.active_providers else "unknown"
        self.input_name = self.session.get_inputs()[0].name
        self.target_size = int(self.session.get_inputs()[0].shape[1])
        self.etiquetas_modelo = cargar_etiquetas_danbooru(self.csv_path)

    def set_model(self, model_path, csv_path=None):
        """Cambia el modelo ONNX (y opcionalmente el CSV de etiquetas)."""
        self._load_model(model_path, csv_path or self.csv_path)

    def set_umbral(self, umbral):
        """Cambia el umbral de confianza para considerar un tag activo."""
        self.umbral_confianza = umbral

    # -- Inferencia interna --------------------------------------------------

    def _postprocesar_probabilidades(self, probabilidades):
        resultados_danbooru = dict(zip(self.etiquetas_modelo, probabilidades))

        etiquetas_originales = sorted(
            [(tag, prob) for tag, prob in resultados_danbooru.items()
             if prob >= self.umbral_confianza],
            key=lambda item: item[1],
            reverse=True,
        )

        tags_finales = []
        confianzas = {}
        for mi_tag, lista_tags_danbooru in MAPEO_PERSONALIZADO.items():
            prob_max = max(
                (resultados_danbooru.get(d_tag, 0.0)
                 for d_tag in lista_tags_danbooru),
                default=0.0,
            )
            if prob_max >= self.umbral_confianza:
                tags_finales.append(mi_tag)
                confianzas[mi_tag] = prob_max

        tags_finales.extend(
            _evaluar_reglas_especiales(resultados_danbooru, self.umbral_confianza),
        )
        tags_finales = sanitizar_tags_frame(tags_finales, confianzas)
        return tags_finales, etiquetas_originales

    def _aplicar_modelo(self, image_path):
        """Ejecuta el modelo sobre una imagen.

        Devuelve ``(tags_finales, etiquetas_originales)``.
        """
        img_tensor = preparar_imagen(image_path, self.target_size)
        probabilidades = self.session.run(
            None, {self.input_name: img_tensor},
        )[0][0]
        return self._postprocesar_probabilidades(probabilidades)

    def _aplicar_modelo_array(self, image_array):
        img_tensor = preparar_imagen_array(image_array, self.target_size)
        probabilidades = self.session.run(
            None, {self.input_name: img_tensor},
        )[0][0]
        return self._postprocesar_probabilidades(probabilidades)

    def _append_frame_tags(self, frames_result, all_tags_ordered, seen_tags, index, timestamp, tags):
        frames_result.append({
            "frame_index": index,
            "timestamp_seconds": round(timestamp, 2),
            "tags": tags,
        })

        for tag_name in tags:
            if tag_name not in seen_tags:
                seen_tags.add(tag_name)
                all_tags_ordered.append(tag_name)

    def _tag_video_detailed_fast(self, video_path, frame_interval, scene_threshold, ffmpeg_bin,
                                 duration_seconds, stop_event=None, frame_count_cb=None):
        frames_result = []
        all_tags_ordered = []
        seen_tags = set()

        if frame_interval == AUTO_FRAME_INTERVAL:
            candidate_interval = compute_auto_candidate_interval(duration_seconds)
            candidate_count = 0
            previous_fingerprint = None
            previous_selected_timestamp = None

            print(
                f"[Video] Modo auto rápido: muestreando frames candidatos en memoria cada {candidate_interval:.2f}s",
                file=sys.stderr,
            )

            for candidate_index, frame_array in _iter_ffmpeg_rawvideo_frames(
                video_path,
                candidate_interval,
                ffmpeg_bin,
                self.target_size,
                stop_event=stop_event,
                startup_timeout_seconds=DEFAULT_FFMPEG_START_TIMEOUT_SECONDS,
                operation="streaming candidate frames",
            ):
                candidate_count += 1
                timestamp = max(0.0, (candidate_index - 1) * candidate_interval)
                if duration_seconds is not None:
                    timestamp = min(timestamp, duration_seconds)

                current_fingerprint = fingerprint_array(frame_array)
                should_select = previous_fingerprint is None
                if not should_select:
                    change_score = frame_change_score(previous_fingerprint, current_fingerprint)
                    forced_by_gap = (
                        previous_selected_timestamp is not None
                        and timestamp - previous_selected_timestamp >= AUTO_FORCE_MAX_GAP_SECONDS
                    )
                    should_select = change_score >= scene_threshold or forced_by_gap
                if not should_select:
                    continue

                tags, _ = self._aplicar_modelo_array(frame_array)
                self._append_frame_tags(
                    frames_result,
                    all_tags_ordered,
                    seen_tags,
                    len(frames_result) + 1,
                    timestamp,
                    tags,
                )
                previous_fingerprint = current_fingerprint
                previous_selected_timestamp = timestamp

            if candidate_count == 0:
                raise RuntimeError("ffmpeg no extrajo ningún frame candidato del vídeo.")

            print(
                f"[Video] Modo auto rápido: {len(frames_result)} frames seleccionados para etiquetar "
                f"(de {candidate_count} candidatos, umbral={scene_threshold:.2f})",
                file=sys.stderr,
            )
        else:
            interval_seconds = float(frame_interval)
            candidate_count = None
            for frame_index, frame_array in _iter_ffmpeg_rawvideo_frames(
                video_path,
                interval_seconds,
                ffmpeg_bin,
                self.target_size,
                stop_event=stop_event,
                startup_timeout_seconds=DEFAULT_FFMPEG_START_TIMEOUT_SECONDS,
                operation="streaming fixed-interval frames",
            ):
                timestamp = max(0.0, (frame_index - 1) * interval_seconds)
                if duration_seconds is not None:
                    timestamp = min(timestamp, duration_seconds)
                tags, _ = self._aplicar_modelo_array(frame_array)
                self._append_frame_tags(
                    frames_result,
                    all_tags_ordered,
                    seen_tags,
                    frame_index,
                    timestamp,
                    tags,
                )

            if not frames_result:
                raise RuntimeError("ffmpeg no extrajo ningún frame del vídeo.")

        if frame_count_cb is not None:
            try:
                frame_count_cb(len(frames_result), candidate_count)
            except Exception:
                pass

        all_tags_ordered = sanitizar_tags_video(all_tags_ordered, frames_result)
        output = {
            "video": str(video_path),
            "frame_interval": str(frame_interval),
            "frame_count": len(frames_result),
            "candidate_count": candidate_count,
            "frames": frames_result,
            "video_tags": all_tags_ordered,
        }
        if duration_seconds is not None:
            output["duration_seconds"] = round(duration_seconds, 2)
        return output

    # -- API pública ---------------------------------------------------------

    def tag_image(self, image_path):
        """Etiqueta una imagen y devuelve la lista de tags finales."""
        tags, _ = self._aplicar_modelo(str(image_path))
        return tags

    def tag_image_detailed(self, image_path):
        """Etiqueta una imagen y devuelve ``(tags_finales, etiquetas_originales)``.

        *etiquetas_originales* es una lista de tuplas ``(tag_danbooru, prob)``
        ordenadas por probabilidad descendente.
        """
        return self._aplicar_modelo(str(image_path))

    def tag_video(self, video_path, frame_interval=AUTO_FRAME_INTERVAL,
                  scene_threshold=DEFAULT_SCENE_THRESHOLD,
                  ffmpeg_bin="ffmpeg", stop_event=None, frame_count_cb=None):
        """Etiqueta un vídeo y devuelve la lista de tags finales."""
        result = self.tag_video_detailed(
            video_path, frame_interval, scene_threshold, ffmpeg_bin,
            stop_event=stop_event, frame_count_cb=frame_count_cb,
        )
        return result["video_tags"]

    def tag_video_detailed(self, video_path, frame_interval=AUTO_FRAME_INTERVAL,
                           scene_threshold=DEFAULT_SCENE_THRESHOLD,
                           ffmpeg_bin="ffmpeg", stop_event=None, frame_count_cb=None):
        """Etiqueta un vídeo y devuelve un dict con información detallada.

        Claves del dict devuelto:
        - ``video``: ruta del vídeo.
        - ``frame_interval``: intervalo utilizado.
        - ``frame_count``: número de frames analizados.
        - ``frames``: lista de dicts por frame (``frame_index``,
          ``timestamp_seconds``, ``tags``).
        - ``video_tags``: lista final de tags del vídeo completo.
        - ``duration_seconds`` (opcional): duración del vídeo si pudo detectarse.
        """
        video_path = Path(video_path)
        duration_seconds = get_video_duration(video_path)

        try:
            return self._tag_video_detailed_fast(
                video_path,
                frame_interval,
                scene_threshold,
                ffmpeg_bin,
                duration_seconds,
                stop_event=stop_event,
                frame_count_cb=frame_count_cb,
            )
        except InterruptedError:
            raise
        except Exception as exc:
            print(
                f"[Video] Ruta rápida en memoria no disponible ({exc}). Se vuelve a la extracción tradicional.",
                file=sys.stderr,
            )

        with tempfile.TemporaryDirectory(prefix="autotagger_frames_") as temp_dir:
            frame_entries, candidate_count = extract_video_frames(
                video_path, Path(temp_dir), frame_interval,
                ffmpeg_bin, scene_threshold, duration_seconds,
                stop_event=stop_event,
            )

            if frame_count_cb is not None:
                try:
                    frame_count_cb(len(frame_entries), candidate_count)
                except Exception:
                    pass
            frames_result = []
            all_tags_ordered = []
            seen_tags = set()

            for index, frame_entry in enumerate(frame_entries, start=1):
                frame_path = frame_entry["path"]
                timestamp = frame_entry["timestamp_seconds"]
                if duration_seconds is not None:
                    timestamp = min(timestamp, duration_seconds)

                tags, _ = self._aplicar_modelo(str(frame_path))

                frames_result.append({
                    "frame_index": index,
                    "timestamp_seconds": round(timestamp, 2),
                    "tags": tags,
                })

                for t in tags:
                    if t not in seen_tags:
                        seen_tags.add(t)
                        all_tags_ordered.append(t)

            all_tags_ordered = sanitizar_tags_video(all_tags_ordered, frames_result)

            output = {
                "video": str(video_path),
                "frame_interval": str(frame_interval),
                "frame_count": len(frames_result),
                "candidate_count": candidate_count,
                "frames": frames_result,
                "video_tags": all_tags_ordered,
            }
            if duration_seconds is not None:
                output["duration_seconds"] = round(duration_seconds, 2)
            return output


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parsear_argumentos():
    parser = argparse.ArgumentParser(
        description="Ejecuta el autotagger sobre una imagen o vídeo de entrada."
    )
    parser.add_argument(
        "input_path",
        help="Ruta de la imagen o vídeo que se quiere procesar."
    )
    parser.add_argument(
        "--frame-interval",
        type=parse_frame_interval,
        default=30.0,
        metavar="SEGUNDOS|auto",
        help="Segundos entre frames extraídos del vídeo, o 'auto' para detección de cambio de escena (por defecto: 30).",
    )
    parser.add_argument(
        "--scene-threshold",
        type=float,
        default=DEFAULT_SCENE_THRESHOLD,
        metavar="0-1",
        help="Sensibilidad para detección de escena en modo auto, de 0 a 1 (por defecto: 0.35).",
    )
    parser.add_argument(
        "--ffmpeg-bin",
        default="ffmpeg",
        metavar="RUTA",
        help="Ruta o nombre del ejecutable ffmpeg (por defecto: ffmpeg).",
    )
    return parser.parse_args()

def _cli_procesar_imagen(tagger, image_path: str):
    """Procesa una imagen individual e imprime los resultados por consola."""
    tags_finales, etiquetas_originales = tagger.tag_image_detailed(image_path)

    print("\n--- ETIQUETAS ORIGINALES DEL MODELO ---")
    texto = ", ".join(f"{tag} ({prob:.2f})" for tag, prob in etiquetas_originales)
    print(texto or "Ninguna")

    print("\n--- RESULTADOS DEL MAPEO ---")
    for tag in tags_finales:
        print(f"[X] {tag.upper()}")

    print(f"\nEtiquetas finales asignadas a la imagen: {tags_finales}")


def _cli_procesar_video(tagger, input_path: Path, args):
    """Procesa un vídeo frame a frame y emite JSON a stdout."""
    duration_seconds = get_video_duration(input_path)

    if args.frame_interval == AUTO_FRAME_INTERVAL:
        print(
            f"[Video] Extrayendo frames por cambio de escena (threshold={args.scene_threshold:.2f})...",
            file=sys.stderr,
        )
    else:
        print(
            f"[Video] Extrayendo frames cada {float(args.frame_interval):.2f}s...",
            file=sys.stderr,
        )

    with tempfile.TemporaryDirectory(prefix="autotagger_frames_") as temp_dir:
        frame_entries = extract_video_frames(
            input_path,
            Path(temp_dir),
            args.frame_interval,
            args.ffmpeg_bin,
            args.scene_threshold,
            duration_seconds,
        )
        print(f"[Video] {len(frame_entries)} frames extraídos", file=sys.stderr)

        frames_result = []
        all_tags_ordered = []
        seen_tags = set()

        for index, frame_entry in enumerate(frame_entries, start=1):
            frame_path = frame_entry["path"]
            timestamp = frame_entry["timestamp_seconds"]

            print(
                f"[Frame] {index}/{len(frame_entries)} @ {timestamp:.1f}s",
                file=sys.stderr,
            )

            tags, originales = tagger.tag_image_detailed(str(frame_path))

            orig_texto = ", ".join(f"{t} ({p:.2f})" for t, p in originales)
            print(f"        Originales: {orig_texto or 'Ninguna'}", file=sys.stderr)
            print(f"        Mapeadas:   {tags}", file=sys.stderr)

            frames_result.append({
                "frame_index": index,
                "timestamp_seconds": round(timestamp, 2),
                "tags": tags,
            })

            for t in tags:
                if t not in seen_tags:
                    seen_tags.add(t)
                    all_tags_ordered.append(t)

        all_tags_ordered = sanitizar_tags_video(all_tags_ordered, frames_result)

        output = {
            "video": str(input_path),
            "frame_interval": str(args.frame_interval),
            "frame_count": len(frames_result),
            "frames": frames_result,
            "video_tags": all_tags_ordered,
        }

        print(json.dumps(output, ensure_ascii=False, indent=2))


def main():
    args = parsear_argumentos()
    input_path = Path(args.input_path)

    print("Cargando modelo y etiquetas...", file=sys.stderr)
    tagger = NSFWAutoTagger()

    if is_video_file(input_path):
        _cli_procesar_video(tagger, input_path, args)
    else:
        print(f"Procesando imagen: {input_path}", file=sys.stderr)
        _cli_procesar_imagen(tagger, str(input_path))


if __name__ == "__main__":
    main()