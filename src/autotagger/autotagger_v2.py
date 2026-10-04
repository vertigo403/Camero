import argparse
import ctypes
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import onnxruntime as rt
import numpy as np
from mutagen import MutagenError
from mutagen.mp4 import MP4
from PIL import Image, ImageOps, ImageStat
import pandas as pd
import cv2

if os.name == "nt":
    from ctypes import wintypes

# --- CONFIGURACIÓN ---
MODEL_PATH = "models/model.onnx"
MODEL_PATH_FALLBACKS = (
    "assets/models/model.onnx",
    "models/model.onnx",
)
CSV_PATH = "assets/selected_tags.csv"
CSV_PATH_FALLBACKS = (
    "assets/selected_tags.csv",
    "models/assets/selected_tags.csv",
    "models/selected_tags.csv",
)
APP_NAME = "AITagger"
APP_COMMAND = "AITagger.exe" if os.name == "nt" else "AITagger"
UMBRAL_CONFIANZA = 0.55  # Threshold de confianza a partir del cual damos un tag por bueno

# --- VÍDEO ---
VIDEO_EXTENSIONS = {
    ".mp4", ".ts", ".mov", ".mkv", ".avi", ".webm", ".flv", ".wmv", ".m4v", ".mpeg", ".mpg",
}
AUTO_FRAME_INTERVAL = "auto"
DEFAULT_SCENE_THRESHOLD = 0.35
DEFAULT_AUTO_CANDIDATE_INTERVAL = 3.0
MAX_AUTO_CANDIDATE_FRAMES = 1200
AUTO_FORCE_MAX_GAP_SECONDS = 120.0
MP4_EXTENSION = ".mp4"
MP4_GENRE_ATOM = "\xa9gen"
MP4_KEYWORD_ATOM = "keyw"
MP4_CATEGORY_ATOM = "catg"
WINDOWS_EXPLORER_PROPERTY_NAMES = (
    "System.Category",
    "System.Keywords",
)
WINDOWS_MAX_PATH = 259
WINDOWS_MAX_FILENAME_LENGTH = 255
PREFERRED_EXECUTION_PROVIDERS = (
    "DmlExecutionProvider",
    "CPUExecutionProvider",
)

ANSI_RESET = "\033[0m"
ANSI_BOLD = "\033[1m"
ANSI_RED = "\033[31m"
ANSI_GREEN = "\033[32m"
ANSI_YELLOW = "\033[33m"
ANSI_BLUE = "\033[34m"
ANSI_CYAN = "\033[36m"

if os.name == "nt":
    HRESULT = ctypes.c_long
    COINIT_APARTMENTTHREADED = 0x2
    S_OK = 0x00000000
    S_FALSE = 0x00000001
    RPC_E_CHANGED_MODE = 0x80010106
    GPS_READWRITE = 0x00000002
    VT_EMPTY = 0x0000

    class GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", wintypes.DWORD),
            ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD),
            ("Data4", ctypes.c_ubyte * 8),
        ]


    class PROPERTYKEY(ctypes.Structure):
        _fields_ = [
            ("fmtid", GUID),
            ("pid", wintypes.DWORD),
        ]


    class CALPWSTR(ctypes.Structure):
        _fields_ = [
            ("cElems", wintypes.ULONG),
            ("pElems", ctypes.POINTER(wintypes.LPWSTR)),
        ]


    class PROPVARIANT_UNION(ctypes.Union):
        _fields_ = [
            ("calpwstr", CALPWSTR),
            ("pointer", ctypes.c_void_p),
        ]


    class PROPVARIANT(ctypes.Structure):
        _anonymous_ = ("value",)
        _fields_ = [
            ("vt", wintypes.USHORT),
            ("wReserved1", wintypes.USHORT),
            ("wReserved2", wintypes.USHORT),
            ("wReserved3", wintypes.USHORT),
            ("value", PROPVARIANT_UNION),
        ]


    class IPropertyStore(ctypes.Structure):
        pass


    QueryInterfaceProto = ctypes.WINFUNCTYPE(
        HRESULT,
        ctypes.POINTER(IPropertyStore),
        ctypes.POINTER(GUID),
        ctypes.POINTER(ctypes.c_void_p),
    )
    AddRefProto = ctypes.WINFUNCTYPE(wintypes.ULONG, ctypes.POINTER(IPropertyStore))
    ReleaseProto = ctypes.WINFUNCTYPE(wintypes.ULONG, ctypes.POINTER(IPropertyStore))
    GetCountProto = ctypes.WINFUNCTYPE(
        HRESULT,
        ctypes.POINTER(IPropertyStore),
        ctypes.POINTER(wintypes.DWORD),
    )
    GetAtProto = ctypes.WINFUNCTYPE(
        HRESULT,
        ctypes.POINTER(IPropertyStore),
        wintypes.DWORD,
        ctypes.POINTER(PROPERTYKEY),
    )
    GetValueProto = ctypes.WINFUNCTYPE(
        HRESULT,
        ctypes.POINTER(IPropertyStore),
        ctypes.POINTER(PROPERTYKEY),
        ctypes.POINTER(PROPVARIANT),
    )
    SetValueProto = ctypes.WINFUNCTYPE(
        HRESULT,
        ctypes.POINTER(IPropertyStore),
        ctypes.POINTER(PROPERTYKEY),
        ctypes.POINTER(PROPVARIANT),
    )
    CommitProto = ctypes.WINFUNCTYPE(HRESULT, ctypes.POINTER(IPropertyStore))


    class IPropertyStoreVtbl(ctypes.Structure):
        _fields_ = [
            ("QueryInterface", QueryInterfaceProto),
            ("AddRef", AddRefProto),
            ("Release", ReleaseProto),
            ("GetCount", GetCountProto),
            ("GetAt", GetAtProto),
            ("GetValue", GetValueProto),
            ("SetValue", SetValueProto),
            ("Commit", CommitProto),
        ]


    IPropertyStore._fields_ = [("lpVtbl", ctypes.POINTER(IPropertyStoreVtbl))]

    OLE32 = ctypes.OleDLL("ole32")
    PROPSYS = ctypes.OleDLL("propsys")
    SHELL32 = ctypes.WinDLL("shell32")

    OLE32.CLSIDFromString.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(GUID)]
    OLE32.CLSIDFromString.restype = HRESULT
    OLE32.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    OLE32.CoInitializeEx.restype = HRESULT
    OLE32.CoUninitialize.argtypes = []
    OLE32.CoUninitialize.restype = None

    PROPSYS.PSGetPropertyKeyFromName.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(PROPERTYKEY)]
    PROPSYS.PSGetPropertyKeyFromName.restype = HRESULT
    PROPSYS.InitPropVariantFromStringVector.argtypes = [
        ctypes.POINTER(wintypes.LPCWSTR),
        wintypes.ULONG,
        ctypes.POINTER(PROPVARIANT),
    ]
    PROPSYS.InitPropVariantFromStringVector.restype = HRESULT
    OLE32.PropVariantClear.argtypes = [ctypes.POINTER(PROPVARIANT)]
    OLE32.PropVariantClear.restype = HRESULT

    SHELL32.SHGetPropertyStoreFromParsingName.argtypes = [
        wintypes.LPCWSTR,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(GUID),
        ctypes.POINTER(ctypes.POINTER(IPropertyStore)),
    ]
    SHELL32.SHGetPropertyStoreFromParsingName.restype = HRESULT


    def _unsigned_hresult(value: int) -> int:
        return ctypes.c_ulong(value).value


    def _raise_for_hresult(value: int, action: str):
        result = _unsigned_hresult(value)
        if result & 0x80000000:
            raise OSError(f"{action} failed with HRESULT 0x{result:08X}: {ctypes.FormatError(result)}")


    def _guid_from_string(value: str) -> GUID:
        guid = GUID()
        _raise_for_hresult(OLE32.CLSIDFromString(value, ctypes.byref(guid)), f"Parsing GUID {value}")
        return guid


    IID_IPROPERTYSTORE = _guid_from_string("{886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99}")


    def _create_string_vector_propvariant(values: list[str]) -> PROPVARIANT:
        propvariant = PROPVARIANT()
        if values:
            array_type = wintypes.LPCWSTR * len(values)
            string_array = array_type(*values)
            _raise_for_hresult(
                PROPSYS.InitPropVariantFromStringVector(string_array, len(values), ctypes.byref(propvariant)),
                "Creating Windows metadata value",
            )
        else:
            propvariant.vt = VT_EMPTY
        return propvariant


    def _open_windows_property_store(file_path: Path):
        property_store = ctypes.POINTER(IPropertyStore)()
        _raise_for_hresult(
            SHELL32.SHGetPropertyStoreFromParsingName(
                str(file_path),
                None,
                GPS_READWRITE,
                ctypes.byref(IID_IPROPERTYSTORE),
                ctypes.byref(property_store),
            ),
            f"Opening Windows property store for {file_path}",
        )
        return property_store


    def _set_windows_property_values(property_store, property_name: str, values: list[str]):
        property_key = PROPERTYKEY()
        _raise_for_hresult(
            PROPSYS.PSGetPropertyKeyFromName(property_name, ctypes.byref(property_key)),
            f"Resolving Windows property {property_name}",
        )
        propvariant = _create_string_vector_propvariant(values)
        try:
            _raise_for_hresult(
                property_store.contents.lpVtbl.contents.SetValue(
                    property_store, ctypes.byref(property_key), ctypes.byref(propvariant)
                ),
                f"Writing Windows property {property_name}",
            )
        finally:
            OLE32.PropVariantClear(ctypes.byref(propvariant))


    def write_windows_explorer_metadata(file_path: Path, tags: list[str]):
        normalized_tags = [tag.strip() for tag in tags if tag.strip()]
        init_result = _unsigned_hresult(OLE32.CoInitializeEx(None, COINIT_APARTMENTTHREADED))
        if init_result not in (S_OK, S_FALSE, RPC_E_CHANGED_MODE):
            _raise_for_hresult(init_result, "Initializing Windows COM")

        should_uninitialize = init_result in (S_OK, S_FALSE)
        property_store = None
        errors = []
        successful_properties = []

        try:
            property_store = _open_windows_property_store(file_path)
            for property_name in WINDOWS_EXPLORER_PROPERTY_NAMES:
                try:
                    _set_windows_property_values(property_store, property_name, normalized_tags)
                    successful_properties.append(property_name)
                except OSError as exc:
                    errors.append(str(exc))

            if not successful_properties:
                error_message = errors[0] if errors else "No writable Windows Explorer metadata property was available."
                raise RuntimeError(error_message)

            _raise_for_hresult(
                property_store.contents.lpVtbl.contents.Commit(property_store),
                f"Saving Windows Explorer metadata for {file_path}",
            )
            return successful_properties
        finally:
            if property_store:
                property_store.contents.lpVtbl.contents.Release(property_store)
            if should_uninitialize:
                OLE32.CoUninitialize()
else:
    def write_windows_explorer_metadata(file_path: Path, tags: list[str]):
        return []


def enable_ansi_colors() -> bool:
    if not sys.stderr.isatty():
        return False

    if os.name != "nt":
        return True

    kernel32 = ctypes.windll.kernel32
    handle = kernel32.GetStdHandle(-12)
    if handle == 0:
        return False

    mode = ctypes.c_uint32()
    if kernel32.GetConsoleMode(handle, ctypes.byref(mode)) == 0:
        return False

    enable_virtual_terminal_processing = 0x0004
    if kernel32.SetConsoleMode(handle, mode.value | enable_virtual_terminal_processing) == 0:
        return False

    return True


class ConsoleUI:
    def __init__(self):
        self.stream = sys.stderr
        self.use_color = enable_ansi_colors()
        self._active_status = False
        self._last_status_width = 0

    def _style(self, text: str, color: str | None = None, *, bold: bool = False) -> str:
        if not self.use_color:
            return text
        prefix = ""
        if bold:
            prefix += ANSI_BOLD
        if color:
            prefix += color
        return f"{prefix}{text}{ANSI_RESET}"

    def _render_progress(self, current: int, total: int, width: int = 20) -> str:
        if total <= 0:
            return "[--------------------] 0/0"
        filled = min(width, int(round(width * (current / total))))
        bar = "#" * filled + "-" * (width - filled)
        percent = int(round((current / total) * 100))
        return f"[{bar}] {current}/{total} {percent:3d}%"

    def _write_status_line(self, text: str):
        padded = text
        padding = max(0, self._last_status_width - len(text))
        if padding:
            padded += " " * padding
        self.stream.write("\r" + padded)
        self.stream.flush()
        self._active_status = True
        self._last_status_width = len(text)

    def clear_status(self):
        if not self._active_status:
            return
        self.stream.write("\r" + " " * self._last_status_width + "\r")
        self.stream.flush()
        self._active_status = False
        self._last_status_width = 0

    def status(self, label: str, message: str, *, progress: tuple[int, int] | None = None):
        prefix = self._style(f"[{label}]", ANSI_CYAN, bold=True)
        parts = [prefix, message]
        if progress is not None:
            parts.append(self._style(self._render_progress(*progress), ANSI_BLUE, bold=True))
        self._write_status_line(" ".join(parts))

    def info(self, label: str, message: str):
        self.clear_status()
        print(f"{self._style(f'[{label}]', ANSI_CYAN, bold=True)} {message}", file=self.stream)

    def success(self, label: str, message: str):
        self.clear_status()
        print(f"{self._style(f'[{label}]', ANSI_GREEN, bold=True)} {message}", file=self.stream)

    def warn(self, label: str, message: str):
        self.clear_status()
        print(f"{self._style(f'[{label}]', ANSI_YELLOW, bold=True)} {message}", file=self.stream)

    def error(self, label: str, message: str):
        self.clear_status()
        print(f"{self._style(f'[{label}]', ANSI_RED, bold=True)} {message}", file=self.stream)


def format_tags(tags: list[str]) -> str:
    return "[" + ", ".join(tags) + "]"


def style_text(text: str, color: str | None = None, *, bold: bool = False, use_color: bool = False) -> str:
    if not use_color:
        return text
    prefix = ""
    if bold:
        prefix += ANSI_BOLD
    if color:
        prefix += color
    return f"{prefix}{text}{ANSI_RESET}"


def build_cli_guide(use_color: bool = False) -> str:
    title = style_text(APP_NAME, ANSI_GREEN, bold=True, use_color=use_color)
    section = lambda text: style_text(text, ANSI_CYAN, bold=True, use_color=use_color)
    command = lambda text: style_text(text, ANSI_BLUE, bold=True, use_color=use_color)

    return "\n".join([
        title,
        "Analyze one image or video and print the final tags as a compact list.",
        "",
        section("Quick Start"),
        f"  {command(f'{APP_COMMAND} <input_path>')}",
        "",
        section("What It Does"),
        "  - Loads the ONNX tagger model once.",
        "  - For images: analyzes the file directly.",
        "  - For videos: extracts frames, analyzes them, and merges the final tags.",
        "  - Prints the result as: [tag1, tag2, tag3]",
        "",
        section("Arguments"),
        f"  {command('input_path')}",
        "      Path to the image or video file you want to analyze.",
        "",
        f"  {command('--frame-interval SECONDS|auto')}",
        "      Video only. Controls how frames are sampled.",
        "      Use a number like 10 or 30 for fixed intervals.",
        "      Use auto to detect scene changes automatically.",
        "      Default: auto",
        "",
        f"  {command('--scene-threshold 0-1')}",
        "      Video only. Used when --frame-interval auto is enabled.",
        "      Lower values select more frames. Higher values select fewer frames.",
        "      Default: 0.35",
        "",
        f"  {command('--ffmpeg-bin PATH')}",
        "      Path to the ffmpeg executable if it is not available in PATH.",
        "      Default: ffmpeg",
        "",
        f"  {command('--rename')}",
        "      Renames the analyzed file by appending the final tags to its filename.",
        "",
        f"  {command('--write-mp4-tags')}",
        "      Video only. Writes the final tags to MP4 genre metadata and Windows Explorer tag properties.",
        "      Only supported for .mp4 inputs.",
        "",
        f"  {command('-h, --help')}",
        "      Show the built-in argparse help screen.",
        "",
        section("Examples"),
        f"  {command(f'{APP_COMMAND} photo.jpg')}",
        f"  {command(f'{APP_COMMAND} clip.mp4 --frame-interval auto')}",
        f"  {command(f'{APP_COMMAND} clip.mp4 --frame-interval 15 --rename')}",
        f"  {command(f'{APP_COMMAND} clip.mp4 --write-mp4-tags')}",
        f"  {command(f'{APP_COMMAND} clip.mp4 --frame-interval auto --scene-threshold 0.40')}",
        "  " + command(f"{APP_COMMAND} clip.mp4 --ffmpeg-bin D:\\FFMPEG\\bin\\ffmpeg.exe"),
        "",
        section("Output"),
        "  - Progress messages are shown in the terminal while processing.",
        "  - The final result is printed as a single tag list.",
        "  - Errors are shown as short, controlled messages.",
        "",
        section("Tips"),
        "  - Start with auto for videos unless you want strict fixed sampling.",
        "  - Use --rename only when you want the original file name to change.",
        "  - Use --write-mp4-tags to write VLC genre metadata and Windows Explorer tags.",
        "  - Use --help for the standard option summary.",
    ])


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
        normalized = str(base_dir)
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
    """Prioriza el modelo empaquetado en assets/models y luego alternativas externas."""
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

    # Personas / Escenas
    #"lesbian": ["multiple_girls", "2girls", "3girls", "4girls"],
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


def is_mp4_file(path: Path) -> bool:
    return path.suffix.lower() == MP4_EXTENSION


def get_video_duration(video_path: Path):
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        return None
    frame_count = cap.get(cv2.CAP_PROP_FRAME_COUNT)
    fps = cap.get(cv2.CAP_PROP_FPS)
    cap.release()
    if fps > 0 and frame_count > 0:
        return frame_count / fps
    return None


def ensure_ffmpeg_available(ffmpeg_bin: str) -> str:
    resolved = shutil.which(ffmpeg_bin)
    if resolved:
        return resolved
    explicit = Path(ffmpeg_bin)
    if explicit.exists():
        return str(explicit)
    print(f"Error: ffmpeg not found: {ffmpeg_bin}", file=sys.stderr)
    print("Install ffmpeg and make sure it is available in PATH, or pass --ffmpeg-bin.", file=sys.stderr)
    sys.exit(1)


def run_ffmpeg_command(command: list[str], operation: str):
    try:
        subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        stderr_output = (exc.stderr or "").strip()
        if stderr_output:
            raise RuntimeError(f"ffmpeg failed while {operation}: {stderr_output}") from None
        raise RuntimeError(f"ffmpeg failed while {operation}.") from None


def parse_frame_interval(value: str):
    stripped = str(value).strip().lower()
    if stripped == AUTO_FRAME_INTERVAL:
        return AUTO_FRAME_INTERVAL
    try:
        interval_seconds = float(stripped)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--frame-interval must be a positive number or 'auto'.") from exc
    if interval_seconds <= 0:
        raise argparse.ArgumentTypeError("--frame-interval must be greater than zero.")
    return interval_seconds


def extract_video_frames_fixed(video_path: Path, output_dir: Path, interval_seconds: float, ffmpeg_bin: str):
    ffmpeg = ensure_ffmpeg_available(ffmpeg_bin)
    output_pattern = output_dir / "frame_%06d.jpg"
    command = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(video_path),
        "-vf", f"fps=1/{interval_seconds}",
        "-q:v", "2",
        str(output_pattern),
    ]
    run_ffmpeg_command(command, "extracting video frames")
    frames = sorted(output_dir.glob("frame_*.jpg"))
    if not frames:
        raise RuntimeError("ffmpeg did not extract any frames from the video.")
    frame_entries = []
    for index, frame_path in enumerate(frames, start=1):
        frame_entries.append({
            "path": frame_path,
            "timestamp_seconds": max(0.0, (index - 1) * interval_seconds),
        })
    return frame_entries


def compute_auto_candidate_interval(duration_seconds) -> float:
    if duration_seconds is None or duration_seconds <= 0:
        return DEFAULT_AUTO_CANDIDATE_INTERVAL
    return max(DEFAULT_AUTO_CANDIDATE_INTERVAL, duration_seconds / MAX_AUTO_CANDIDATE_FRAMES)


def fingerprint_image(image_path: Path):
    with Image.open(image_path) as image:
        grayscale = ImageOps.grayscale(image).resize((32, 32), Image.Resampling.BILINEAR)
        pixel_data = grayscale.get_flattened_data if hasattr(grayscale, "get_flattened_data") else grayscale.getdata
        pixels = list(pixel_data())
        mean_value = sum(pixels) / len(pixels)
        bits = tuple(pixel >= mean_value for pixel in pixels)
        stats = ImageStat.Stat(grayscale)
        return {
            "bits": bits,
            "mean": stats.mean[0],
            "stddev": stats.stddev[0],
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


def extract_video_frames_auto(
    video_path: Path, output_dir: Path, scene_threshold: float, ffmpeg_bin: str, duration_seconds
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
        "-i", str(video_path),
        "-vf", f"fps=1/{candidate_interval}",
        "-q:v", "2",
        str(output_pattern),
    ]
    run_ffmpeg_command(command, "extracting candidate video frames")
    candidate_frames = sorted(candidates_dir.glob("frame_*.jpg"))
    if not candidate_frames:
        raise RuntimeError("ffmpeg did not extract any candidate frames from the video.")

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

    return frame_entries


def extract_video_frames(video_path, output_dir, frame_interval, ffmpeg_bin, scene_threshold, duration_seconds):
    if frame_interval == AUTO_FRAME_INTERVAL:
        return extract_video_frames_auto(video_path, output_dir, scene_threshold, ffmpeg_bin, duration_seconds)
    return extract_video_frames_fixed(video_path, output_dir, float(frame_interval), ffmpeg_bin)


def _serialize_mp4_text_values(values: list[str]) -> str:
    return ";".join(value.strip() for value in values if value.strip())


def _normalize_mp4_text_values(values: list[str]) -> list[str]:
    normalized_values = []
    for value in values:
        normalized_values.extend(
            part.strip()
            for part in str(value).split(";")
            if part.strip()
        )
    return normalized_values


def write_mp4_genres_metadata(video_path: Path, genres: list[str]) -> list[str]:
    if not is_mp4_file(video_path):
        raise ValueError("Metadata writing is only supported for MP4 input files.")

    try:
        mp4_file = MP4(str(video_path))
        if genres:
            serialized_genres = _serialize_mp4_text_values(genres)
            serialized_payload = [serialized_genres] if serialized_genres else []
            mp4_file[MP4_GENRE_ATOM] = serialized_payload
            mp4_file[MP4_KEYWORD_ATOM] = serialized_payload
            mp4_file[MP4_CATEGORY_ATOM] = serialized_payload
        elif mp4_file.tags:
            for atom_name in (MP4_GENRE_ATOM, MP4_KEYWORD_ATOM, MP4_CATEGORY_ATOM):
                if atom_name in mp4_file.tags:
                    del mp4_file[atom_name]
        mp4_file.save()

        if os.name == "nt":
            write_windows_explorer_metadata(video_path, genres)

        reloaded = MP4(str(video_path))
        stored_genres = reloaded.tags.get(MP4_KEYWORD_ATOM, []) if reloaded.tags else []
        return _normalize_mp4_text_values(stored_genres)
    except MutagenError as exc:
        raise RuntimeError(f"Could not update MP4 metadata: {exc}") from exc


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
        self.target_size = self.session.get_inputs()[0].shape[1]
        self.etiquetas_modelo = cargar_etiquetas_danbooru(self.csv_path)

    def set_model(self, model_path, csv_path=None):
        """Cambia el modelo ONNX (y opcionalmente el CSV de etiquetas)."""
        self._load_model(model_path, csv_path or self.csv_path)

    def set_umbral(self, umbral):
        """Cambia el umbral de confianza para considerar un tag activo."""
        self.umbral_confianza = umbral

    # -- Inferencia interna --------------------------------------------------

    def _aplicar_modelo(self, image_path):
        """Ejecuta el modelo sobre una imagen.

        Devuelve ``(tags_finales, etiquetas_originales)``.
        """
        img_tensor = preparar_imagen(image_path, self.target_size)
        probabilidades = self.session.run(
            None, {self.input_name: img_tensor},
        )[0][0]
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
                  ffmpeg_bin="ffmpeg"):
        """Etiqueta un vídeo y devuelve la lista de tags finales."""
        result = self.tag_video_detailed(
            video_path, frame_interval, scene_threshold, ffmpeg_bin,
        )
        return result["video_tags"]

    def tag_video_detailed(self, video_path, frame_interval=AUTO_FRAME_INTERVAL,
                           scene_threshold=DEFAULT_SCENE_THRESHOLD,
                           ffmpeg_bin="ffmpeg",
                           progress_callback=None):
        """Etiqueta un vídeo y devuelve un dict con información detallada.

        Claves del dict devuelto:
        - ``video``: ruta del vídeo.
        - ``frame_interval``: intervalo utilizado.
        - ``frame_count``: número de frames analizados.
        - ``frames``: lista de dicts por frame (``frame_index``,
          ``timestamp_seconds``, ``tags``).
        - ``video_tags``: lista final de tags del vídeo completo.
        - ``duration_seconds`` (opcional): duración del vídeo.
        """
        video_path = Path(video_path)
        duration_seconds = get_video_duration(video_path)

        with tempfile.TemporaryDirectory(prefix="autotagger_frames_") as temp_dir:
            frame_entries = extract_video_frames(
                video_path, Path(temp_dir), frame_interval,
                ffmpeg_bin, scene_threshold, duration_seconds,
            )

            frames_result = []
            all_tags_ordered = []
            seen_tags = set()

            for index, frame_entry in enumerate(frame_entries, start=1):
                frame_path = frame_entry["path"]
                timestamp = frame_entry["timestamp_seconds"]
                if duration_seconds is not None:
                    timestamp = min(timestamp, duration_seconds)

                if progress_callback is not None:
                    progress_callback(index - 1, len(frame_entries), timestamp)

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

            if progress_callback is not None:
                progress_callback(len(frame_entries), len(frame_entries), duration_seconds)

            all_tags_ordered = sanitizar_tags_video(all_tags_ordered, frames_result)

            output = {
                "video": str(video_path),
                "frame_interval": str(frame_interval),
                "frame_count": len(frames_result),
                "frames": frames_result,
                "video_tags": all_tags_ordered,
            }
            if duration_seconds is not None:
                output["duration_seconds"] = round(duration_seconds, 2)

            return output


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_argument_parser():
    parser = argparse.ArgumentParser(
        prog=APP_COMMAND,
        description=(
            f"Run {APP_NAME} on an input image or video.\n\n"
            "The program prints final tags as a compact list like: [tag1, tag2, tag3]."
        ),
        epilog=(
            "Examples:\n"
            f"  {APP_COMMAND} photo.jpg\n"
            f"  {APP_COMMAND} clip.mp4 --frame-interval auto\n"
            f"  {APP_COMMAND} clip.mp4 --frame-interval 15 --rename\n"
            f"  {APP_COMMAND} clip.mp4 --frame-interval auto --scene-threshold 0.40\n"
            f"  {APP_COMMAND} clip.mp4 --ffmpeg-bin D:\\FFMPEG\\bin\\ffmpeg.exe"
        ),
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        "input_path",
        help="Path to the image or video to process."
    )
    parser.add_argument(
        "--frame-interval",
        type=parse_frame_interval,
        default=AUTO_FRAME_INTERVAL,
        metavar="SECONDS|auto",
        help="Seconds between extracted video frames, or 'auto' for scene-change detection (default: auto).",
    )
    parser.add_argument(
        "--scene-threshold",
        type=float,
        default=DEFAULT_SCENE_THRESHOLD,
        metavar="0-1",
        help="Scene detection sensitivity in auto mode, from 0 to 1 (default: 0.35).",
    )
    parser.add_argument(
        "--ffmpeg-bin",
        default="ffmpeg",
        metavar="PATH",
        help="Path or executable name for ffmpeg (default: ffmpeg).",
    )
    parser.add_argument(
        "--rename",
        action="store_true",
        help="Rename the input file by appending tags to the filename, separated by '-'.",
    )
    parser.add_argument(
        "--write-mp4-tags",
        action="store_true",
        help="Write final tags to MP4 genre metadata (©gen) and to Windows Explorer tag properties. Only supported for .mp4 videos.",
    )
    return parser


def parsear_argumentos(argv=None):
    parser = build_argument_parser()
    return parser.parse_args(argv)


def validate_input_path(input_path: Path):
    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")
    if not input_path.is_file():
        raise FileNotFoundError(f"Input path is not a file: {input_path}")


def validate_metadata_options(input_path: Path, args):
    if not args.write_mp4_tags:
        return
    if not is_mp4_file(input_path):
        raise ValueError("--write-mp4-tags requires an MP4 video input.")


def _max_longitud_nombre(file_path: Path) -> int:
    parent_str = str(file_path.parent)
    separator_length = 0 if parent_str.endswith(("\\", "/")) else 1
    max_by_path = WINDOWS_MAX_PATH - len(parent_str) - separator_length
    return max(1, min(WINDOWS_MAX_FILENAME_LENGTH, max_by_path))


def _construir_nombre_renombrado(file_path: Path, tags: list[str]) -> tuple[str, int]:
    max_name_length = _max_longitud_nombre(file_path)
    suffix = file_path.suffix
    available_length = max_name_length - len(suffix)
    if available_length <= 0:
        raise OSError("The path is too long to build a new filename.")

    selected_tags = []
    suffix_text = ""
    for tag in tags:
        candidate_suffix = f"{suffix_text}-{tag}"
        if len(candidate_suffix) >= available_length:
            break
        selected_tags.append(tag)
        suffix_text = candidate_suffix

    stem_budget = max(1, available_length - len(suffix_text))
    truncated_stem = file_path.stem[:stem_budget].rstrip(" ._") or file_path.stem[:stem_budget] or "file"
    new_name = f"{truncated_stem}{suffix_text}{suffix}"
    omitted_count = len(tags) - len(selected_tags)
    return new_name, omitted_count


def _resolver_colision_renombrado(file_path: Path, new_name: str) -> Path:
    candidate_path = file_path.with_name(new_name)
    if candidate_path == file_path or not candidate_path.exists():
        return candidate_path

    suffix = file_path.suffix
    candidate_stem = candidate_path.stem
    max_name_length = _max_longitud_nombre(file_path)

    for index in range(2, 10000):
        counter_suffix = f"_{index}"
        stem_budget = max_name_length - len(suffix) - len(counter_suffix)
        if stem_budget <= 0:
            break
        truncated_stem = candidate_stem[:stem_budget].rstrip(" ._") or candidate_stem[:stem_budget] or "file"
        resolved_name = f"{truncated_stem}{counter_suffix}{suffix}"
        resolved_path = file_path.with_name(resolved_name)
        if not resolved_path.exists():
            return resolved_path

    raise FileExistsError("Could not generate a unique filename within the length limit.")


def _renombrar_archivo(file_path: Path, tags: list, /):
    """Renombra *file_path* añadiendo los tags al stem, separados por '-'.

    Devuelve la nueva ruta del archivo.
    """
    if not tags:
        return file_path, False, 0

    new_name, omitted_count = _construir_nombre_renombrado(file_path, tags)
    new_path = _resolver_colision_renombrado(file_path, new_name)
    if new_path == file_path:
        return file_path, False, 0

    file_path.rename(new_path)
    return new_path, True, omitted_count


def _cli_procesar_imagen(tagger, image_path: str, ui: ConsoleUI, rename: bool = False):
    """Process an image and print only the final tags."""
    ui.status("Image", "Analyzing image")
    tags_finales, _ = tagger.tag_image_detailed(image_path)
    ui.success("Image", f"Completed with {len(tags_finales)} tags")
    print(format_tags(tags_finales))

    if rename:
        new_path, renamed, omitted_count = _renombrar_archivo(Path(image_path), tags_finales)
        if renamed:
            rename_message = f"Renamed file to {new_path.name}"
            if omitted_count > 0:
                rename_message += f" ({omitted_count} tags omitted to respect filename length limits)"
            ui.success("Rename", rename_message)
        else:
            ui.info("Rename", "Filename already contained the final tags")


def _cli_procesar_video(tagger, input_path: Path, args, ui: ConsoleUI):
    """Process a video and print only the final tags."""
    duration_seconds = get_video_duration(input_path)
    if duration_seconds is not None:
        ui.info("Video", f"Duration: {duration_seconds:.2f}s")

    if args.frame_interval == AUTO_FRAME_INTERVAL:
        ui.status("Video", f"Extracting frames in auto mode (threshold={args.scene_threshold:.2f})")
    else:
        ui.status("Video", f"Extracting frames every {float(args.frame_interval):.2f}s")

    with tempfile.TemporaryDirectory(prefix="autotagger_frames_") as temp_dir:
        frame_entries = extract_video_frames(
            input_path,
            Path(temp_dir),
            args.frame_interval,
            args.ffmpeg_bin,
            args.scene_threshold,
            duration_seconds,
        )
        ui.info("Video", f"Selected {len(frame_entries)} frames for analysis")

        frames_result = []
        all_tags_ordered = []
        seen_tags = set()

        for index, frame_entry in enumerate(frame_entries, start=1):
            frame_path = frame_entry["path"]
            timestamp = frame_entry["timestamp_seconds"]
            if duration_seconds is not None:
                timestamp = min(timestamp, duration_seconds)

            ui.status(
                "Video",
                f"Analyzing frames around {timestamp:.1f}s",
                progress=(index, len(frame_entries)),
            )

            tags, _ = tagger.tag_image_detailed(str(frame_path))

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

        ui.success("Video", f"Completed with {len(all_tags_ordered)} final tags")
        print(format_tags(all_tags_ordered))

        if args.write_mp4_tags:
            try:
                stored_genres = write_mp4_genres_metadata(input_path, all_tags_ordered)
            except Exception as exc:
                ui.error("Metadata", f"Could not update MP4 metadata: {exc}")
            else:
                ui.success("Metadata", f"Updated MP4 genres and Windows Explorer tags: {format_tags(stored_genres)}")

        if args.rename:
            new_path, renamed, omitted_count = _renombrar_archivo(input_path, all_tags_ordered)
            if renamed:
                rename_message = f"Renamed file to {new_path.name}"
                if omitted_count > 0:
                    rename_message += f" ({omitted_count} tags omitted to respect filename length limits)"
                ui.success("Rename", rename_message)
            else:
                ui.info("Rename", "Filename already contained the final tags")


def main():
    ui = ConsoleUI()

    if len(sys.argv) == 1:
        print(build_cli_guide(ui.use_color))
        sys.exit(0)

    args = parsear_argumentos()
    input_path = Path(args.input_path)

    try:
        validate_input_path(input_path)
        validate_metadata_options(input_path, args)
        ui.status("Model", "Loading model and tag definitions")
        tagger = NSFWAutoTagger()
        ui.success("Model", f"Ready on {tagger.active_provider}")
        ui.info("ONNX", f"Available providers: {', '.join(tagger.available_providers)}")

        if is_video_file(input_path):
            _cli_procesar_video(tagger, input_path, args, ui)
        else:
            _cli_procesar_imagen(tagger, str(input_path), ui, rename=args.rename)
    except KeyboardInterrupt:
        ui.error("Abort", "Operation cancelled by user")
        sys.exit(130)
    except Exception as exc:
        ui.error("Error", str(exc))
        sys.exit(1)


if __name__ == "__main__":
    main()