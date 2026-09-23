# -*- coding: utf-8 -*-
"""
Matchering 2.0 - 音频参考母带、Ozone 12 (KS预设) 音质增强与原曲直通格式转换批量工作台 (GUI)
基于 PyQt6 + Matchering 核心算法 + Spotify Pedalboard (Ozone 12 VST3) 打造
特性：
- 三选项卡架构：
  1) 选项卡 1：Matchering 目标参考母带（基于高品质参考曲目匹配频响与动态）
  2) 选项卡 2：iZotope Ozone 12 (KS 预设) 离线批量增强母带（无需参考歌曲，纯正专业母带链提升音质）
  3) 选项卡 3：原曲纯净直通（不做任何效果修改与压限，原汁原味转换格式并继承内嵌封面与元数据）
- 5 档精细输出响度调节（-6dB, -4.5dB, -3.0dB, -1.5dB, 0dB，告别过度压限与炸耳）
- 24-bit 96kHz FLAC / 320kbps MP3 / 192kbps MP3 / 24-bit WAV 等丰富格式导出
- 智能匹配并等比压缩内嵌同目录音乐同名封面（如 歌曲名.jpg）及无损继承源文件内嵌封面
- 全局音乐标签元数据（歌曲名、音乐家、年代、专辑、流派、音轨号）继承与持久化自动记忆
"""

import os
import sys
import io
import json
import time
import traceback
import xml.etree.ElementTree as ET
from pathlib import Path

# 确保 .venv/Scripts 下的 ffmpeg 可被全局调用
scripts_dir = os.path.dirname(sys.executable)
if scripts_dir not in os.environ.get("PATH", ""):
    os.environ["PATH"] = scripts_dir + os.pathsep + os.environ.get("PATH", "")

import numpy as np
import soundfile as sf
from PIL import Image
from mutagen.flac import FLAC, Picture
from mutagen.wave import WAVE
from mutagen.mp3 import MP3
from mutagen.id3 import ID3, TIT2, TPE1, TALB, TCON, TDRC, APIC, TRCK, TPE2

try:
    import pedalboard
    import pedalboard.io
except ImportError:
    pedalboard = None

from PyQt6.QtCore import (
    Qt, QThread, pyqtSignal, QUrl
)
from PyQt6.QtGui import (
    QColor, QDesktopServices, QDragEnterEvent, QDropEvent
)
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QLineEdit, QFileDialog, QTableWidget,
    QTableWidgetItem, QHeaderView, QProgressBar, QTextEdit,
    QComboBox, QCheckBox, QGroupBox, QMessageBox,
    QFrame, QAbstractItemView, QButtonGroup, QTabWidget,
    QDialog, QScrollArea
)

import matchering as mg
from matchering.results import Result

CONFIG_FILE = os.path.abspath(os.path.join(os.path.dirname(__file__), "config_settings.json"))
OZONE_VST3_DIR_DEFAULT = r"C:\Program Files\Common Files\VST3\iZotope"
OZONE_KS_XML_DEFAULT = os.path.expanduser(r"~\Documents\iZotope\Ozone\User Presets\Global Presets\KS.xml")

SUPPORTED_EXTENSIONS = {
    ".wav", ".mp3", ".flac", ".ogg", ".aiff", ".aif", ".m4a", ".aac", ".wma"
}

AUDIO_FILTER_STR = (
    "音频文件 (*.wav *.mp3 *.flac *.ogg *.aiff *.aif *.m4a *.aac *.wma);;"
    "FLAC 文件 (*.flac);;WAV 文件 (*.wav);;MP3 文件 (*.mp3);;所有文件 (*.*)"
)

# 5 档响度配置定义
LOUDNESS_LEVELS = [
    {"level": 1, "db": -6.0, "name": "1档: 极柔和 (-6dB)", "desc": "最大动态空间，听感极其通透宽松（适合古典/爵士/Hi-Fi原声）"},
    {"level": 2, "db": -4.5, "name": "2档: 温和 (-4.5dB)", "desc": "显著降低母带压限紧绷感，消除听觉疲劳"},
    {"level": 3, "db": -3.0, "name": "3档: 流媒体推荐 (-3dB)", "desc": "防过载爆音，符合各大流媒体响度规范（推荐首选）"},
    {"level": 4, "db": -1.5, "name": "4档: 微调微降 (-1.5dB)", "desc": "留出 True-Peak 净空，同时保留充足冲击力"},
    {"level": 5, "db": 0.0,  "name": "5档: 原味匹配 (0dB)", "desc": "算法原始默认，100% 对齐商业压限响度"}
]


class CancelledException(Exception):
    """用户主动取消任务异常"""
    pass


# 输出音频格式及采样率档位定义
AUDIO_FORMATS = {
    # 格式代码: (扩展名, soundfile子类型/码率, 目标采样率(Hz或None表示保持原曲), 界面显示名称)
    "FLAC_24_96": (".flac", "PCM_24", 96000, "24-bit / 96 kHz FLAC (高清母带级 / 推荐默认)"),
    "FLAC_24_48": (".flac", "PCM_24", 48000, "24-bit / 48 kHz FLAC (专业演播级)"),
    "FLAC_24_44": (".flac", "PCM_24", 44100, "24-bit / 44.1 kHz FLAC (标准高保真)"),
    "FLAC_24":    (".flac", "PCM_24", None,  "24-bit FLAC (保持原始采样率)"),
    "FLAC_16_44": (".flac", "PCM_16", 44100, "16-bit / 44.1 kHz FLAC (标准 CD 级)"),
    "FLAC_16":    (".flac", "PCM_16", None,  "16-bit FLAC (保持原始采样率)"),
    "MP3_320":    (".mp3",  "320",    None,  "320 kbps MP3 (最高品质压缩 / 广泛兼容)"),
    "MP3_192":    (".mp3",  "192",    None,  "192 kbps MP3 (标准品质压缩 / 节省体积)"),
    "WAV_24_96":  (".wav",  "PCM_24", 96000, "24-bit / 96 kHz WAV (高清母带级)"),
    "WAV_24_48":  (".wav",  "PCM_24", 48000, "24-bit / 48 kHz WAV (专业演播级)"),
    "WAV_24":     (".wav",  "PCM_24", None,  "24-bit WAV (保持原始采样率)"),
    "WAV_16_44":  (".wav",  "PCM_16", 44100, "16-bit / 44.1 kHz WAV (标准 CD 级)"),
    "WAV_16":     (".wav",  "PCM_16", None,  "16-bit WAV (保持原始采样率)"),
    "WAV_FLOAT":  (".wav",  "FLOAT",  None,  "32-bit Float WAV (高动态范围 / 浮点)")
}
DEFAULT_AUDIO_FORMAT = "FLAC_24_96"


def get_mp3_compatible_sr(sr: int) -> int:
    """MP3 格式最高支持 48000Hz 采样率。若原采样率超过 48000Hz，智能选择 48000Hz 或 44100Hz"""
    if sr <= 48000:
        return sr
    if sr % 44100 == 0:
        return 44100
    return 48000


def resample_audio(audio: np.ndarray, orig_sr: int, target_sr: int) -> np.ndarray:
    """
    使用 SciPy 高阶多相滤波 (Polyphase Filtering) 高保真重采样音频。
    audio 支持 (channels, samples) 或 (samples, channels) 格式
    """
    if orig_sr == target_sr or target_sr is None or orig_sr <= 0 or target_sr <= 0:
        return audio

    import math
    import scipy.signal as sps

    is_transposed = False
    if audio.ndim == 2 and audio.shape[0] > audio.shape[1]:
        audio_data = audio.T
        is_transposed = True
    else:
        audio_data = audio

    gcd = math.gcd(int(orig_sr), int(target_sr))
    up = int(target_sr) // gcd
    down = int(orig_sr) // gcd

    resampled = sps.resample_poly(audio_data, up, down, axis=-1).astype(np.float32)

    if is_transposed:
        return resampled.T
    return resampled


def save_audio_file(file_path: str, audio: np.ndarray, sample_rate: int, format_code: str):
    """
    统一音频文件导出函数：
    - 对 FLAC / WAV: 使用 soundfile 高保真导出
    - 对 MP3: 使用 pedalboard.io.AudioFile 进行精确 CBR 320k / 192k 编码
    audio 支持 (channels, samples) 或 (samples, channels)
    """
    fmt_info = AUDIO_FORMATS.get(format_code, AUDIO_FORMATS[DEFAULT_AUDIO_FORMAT])
    ext, subtype, target_sr, format_display = fmt_info

    if audio.ndim == 1:
        audio = np.vstack([audio, audio])

    if audio.shape[0] > audio.shape[1]:
        audio_cs = audio.T
        audio_sc = audio
    else:
        audio_cs = audio
        audio_sc = audio.T

    # 防削波裁剪保护
    audio_cs = np.clip(audio_cs, -1.0, 1.0)
    audio_sc = np.clip(audio_sc, -1.0, 1.0)

    if ext == ".mp3":
        quality = 320 if subtype == "320" else 192
        sr = get_mp3_compatible_sr(sample_rate)
        if sr != sample_rate:
            audio_cs = resample_audio(audio_cs, sample_rate, sr)
        if pedalboard is not None:
            with pedalboard.io.AudioFile(file_path, "w", samplerate=sr, num_channels=audio_cs.shape[0], quality=quality) as f:
                f.write(audio_cs.astype(np.float32))
        else:
            sf.write(file_path, audio_sc, sr, format="MP3")
    else:
        sf.write(file_path, audio_sc, sample_rate, subtype=subtype)



# ==============================================================================
# iZotope Ozone 原生 XML 预设解析与 VST3 离线母带链构建
# ==============================================================================
def parse_ozone_preset_info(xml_path: str) -> dict:
    """快速解析 Ozone XML 预设文件，提取已激活模块及核心参数供 UI 与日志展示"""
    info = {
        "valid": False,
        "filename": os.path.basename(xml_path) if xml_path else "",
        "path": xml_path,
        "modules": [],
        "error": ""
    }
    if not xml_path or not os.path.exists(xml_path):
        info["error"] = "预设文件不存在"
        return info

    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()

        # 1. Bass Control
        bc = root.find("BassControl")
        if bc is not None and bc.attrib.get("Enabled") == "1":
            bal, punch, sus = 0.0, 0.0, 0.0
            for p in bc.findall("Param"):
                pid = p.attrib.get("ParamID")
                v = float(p.attrib.get("Value", 0))
                if pid == "Balance": bal = v
                elif pid == "Punch": punch = v
                elif pid == "SustainPowerAmount": sus = v
            info["modules"].append({
                "name": "Bass Control",
                "tag": "低频控制",
                "icon": "🎚️",
                "brief": f"Bal {bal:+.1f}dB | Pn {punch:+.2f} | Sus {sus:.1f}",
                "detail": f"低频平衡: {bal:+.1f}dB, 打击力度: {punch:+.2f}dB, 延音能量: {sus:.1f}"
            })

        # 2. Dynamic EQ
        deq = root.find("DynamicEQ")
        if deq is not None and deq.attrib.get("Enabled") == "1":
            bands = {}
            for p in deq.findall("Param"):
                pid = p.attrib.get("ParamID", "")
                val = float(p.attrib.get("Value", 0))
                for b in range(1, 7):
                    prefix = f"Band {b} "
                    if pid.startswith(prefix):
                        bands.setdefault(b, {})[pid[len(prefix):]] = val
            band_strs = []
            for b, d in sorted(bands.items()):
                if d.get("Enable", 1) and "Frequency" in d and "Gain" in d:
                    band_strs.append(f"{d['Frequency']:.0f}Hz({d['Gain']:+.1f}dB)")
            info["modules"].append({
                "name": "Dynamic EQ",
                "tag": "动态均衡",
                "icon": "📈",
                "brief": f"{len(band_strs)} 动态频段活跃",
                "detail": "动态频段: " + (", ".join(band_strs) if band_strs else "默认")
            })

        # 3. Clarity
        cla = root.find("Clarity")
        if cla is not None and cla.attrib.get("Enabled") == "1":
            amt, tilt, att, rel = 0.0, 0.0, 0.0, 0.0
            for p in cla.findall("Param"):
                pid = p.attrib.get("ParamID")
                v = float(p.attrib.get("Value", 0))
                if pid == "Amount": amt = v
                elif pid == "Attack": att = v
                elif pid == "Tilt": tilt = v
                elif pid == "Release": rel = v
            info["modules"].append({
                "name": "Clarity",
                "tag": "清晰度",
                "icon": "✨",
                "brief": f"Amount {amt:.1f} | Tilt {tilt:.2f}",
                "detail": f"清晰度强度: {amt:.1f}, 倾斜度: {tilt:.2f}, 启动: {att:.0f}ms, 释放: {rel:.0f}ms"
            })

        # 4. Equalizer
        eq = root.find("EQ")
        if eq is not None and eq.attrib.get("Enabled") == "1":
            bands = {}
            for p in eq.findall("Param"):
                pid = p.attrib.get("ParamID", "")
                val = float(p.attrib.get("Value", 0))
                for b in range(1, 9):
                    prefix = f"Band {b} "
                    if pid.startswith(prefix):
                        bands.setdefault(b, {})[pid[len(prefix):]] = val
            band_strs = []
            for b, d in sorted(bands.items()):
                if d.get("Enable", 1) and "Frequency" in d and "Gain" in d:
                    band_strs.append(f"{d['Frequency']:.0f}Hz({d['Gain']:+.1f}dB)")
            info["modules"].append({
                "name": "Equalizer",
                "tag": "母带EQ",
                "icon": "🎛️",
                "brief": f"{len(band_strs)} EQ频段精调",
                "detail": "EQ频段: " + (", ".join(band_strs) if band_strs else "平直")
            })

        # 5. Low End Focus
        lef = root.find("LowEndFocus")
        if lef is not None and lef.attrib.get("Enabled") == "1":
            gain, strg = 0.0, 0.0
            for p in lef.findall("Param"):
                pid = p.attrib.get("ParamID")
                v = float(p.attrib.get("Value", 0))
                if pid == "Gain": gain = v
                elif pid == "Strength": strg = v
            info["modules"].append({
                "name": "Low End Focus",
                "tag": "低频对焦",
                "icon": "🔍",
                "brief": f"Gain {gain:+.2f}dB | Str {strg:.1f}",
                "detail": f"增益: {gain:+.2f}dB, 锐化强度: {strg:.1f}"
            })

        # 6. Maximizer
        maxi = root.find("Maximizer")
        if maxi is not None and maxi.attrib.get("Enabled") == "1":
            info["modules"].append({
                "name": "Maximizer",
                "tag": "商业限制器",
                "icon": "🚀",
                "brief": "IRC IV 智能防削波 | 天花板 -0.3dB",
                "detail": "IRC IV 智能透明防削波母带限制器 (True-Peak -0.3dBFS, 动态驱动 +2.0dB)"
            })

        info["valid"] = True
    except Exception as e:
        info["error"] = str(e)
    return info


def create_ozone_chain_from_xml(xml_path: str, vst_dir: str = OZONE_VST3_DIR_DEFAULT):
    """基于 Ozone 原生 XML 预设，精准加载 iZotope 独立 VST3 模块构建离线 DSP 母带流水线"""
    if not pedalboard:
        raise RuntimeError("未安装 pedalboard 模块，无法使用 VST3 引擎！")
    if not os.path.exists(xml_path):
        raise FileNotFoundError(f"未找到 Ozone 预设文件: {xml_path}")
    if not os.path.exists(vst_dir):
        raise FileNotFoundError(f"未找到 iZotope VST3 目录: {vst_dir}")

    tree = ET.parse(xml_path)
    root = tree.getroot()
    chain_modules = []
    summary_list = []

    # 1. Bass Control
    bc_elem = root.find("BassControl")
    bc_path = os.path.join(vst_dir, "Ozone 12 Bass Control.vst3")
    if bc_elem is not None and bc_elem.attrib.get("Enabled") == "1" and os.path.exists(bc_path):
        bc = pedalboard.load_plugin(bc_path)
        bc.bas_balance_bypass = False
        bc.bas_punch_bypass = False
        bc.bas_sustain_power_bypass = False
        for p in bc_elem.findall("Param"):
            pid = p.attrib.get("ParamID")
            val = float(p.attrib.get("Value", 0))
            if pid == "Balance": bc.bas_balance_db = val
            elif pid == "Punch": bc.bas_punch_db = val
            elif pid == "SustainPowerAmount": bc.bas_sustain_power_amount = val
        desc_str = f"Balance={bc.bas_balance_db:+.1f}dB, Punch={bc.bas_punch_db:+.2f}dB, Sustain={bc.bas_sustain_power_amount:.1f}"
        chain_modules.append(("Ozone 12 Bass Control", bc, "低频控制 (Bass Control)", desc_str))
        summary_list.append(f"Bass Control ({desc_str})")

    # 2. Dynamic EQ
    deq_elem = root.find("DynamicEQ")
    deq_path = os.path.join(vst_dir, "Ozone 12 Dynamic EQ.vst3")
    if deq_elem is not None and deq_elem.attrib.get("Enabled") == "1" and os.path.exists(deq_path):
        deq = pedalboard.load_plugin(deq_path)
        bands = {}
        for p in deq_elem.findall("Param"):
            pid = p.attrib.get("ParamID", "")
            val = float(p.attrib.get("Value", 0))
            for b_idx in range(1, 7):
                prefix = f"Band {b_idx} "
                if pid.startswith(prefix):
                    bands.setdefault(b_idx, {})[pid[len(prefix):]] = val
        band_descs = []
        for b_idx, b_data in sorted(bands.items()):
            if b_data.get("Enable", 1):
                setattr(deq, f"dyneq_stereo_main_enable_{b_idx}", True)
                freq = b_data.get("Frequency", 1000.0)
                gain = b_data.get("Gain", 0.0)
                q = b_data.get("Q", 1.0)
                setattr(deq, f"dyneq_stereo_main_frequency_{b_idx}_hz", freq)
                setattr(deq, f"dyneq_stereo_main_gain_{b_idx}_db", gain)
                setattr(deq, f"dyneq_stereo_main_q_{b_idx}", q)
                if "Shape" in b_data:
                    param_name = f"dyneq_stereo_main_shape_{b_idx}"
                    if param_name in deq.parameters:
                        p_obj = deq.parameters[param_name]
                        idx = int(b_data["Shape"])
                        if hasattr(p_obj, "valid_values") and 0 <= idx < len(p_obj.valid_values):
                            setattr(deq, param_name, p_obj.valid_values[idx])
                band_descs.append(f"{freq:.0f}Hz({gain:+.1f}dB)")
        desc_str = f"{len(band_descs)} 个动态频段: {', '.join(band_descs)}"
        chain_modules.append(("Ozone 12 Dynamic EQ", deq, "动态均衡 (Dynamic EQ)", desc_str))
        summary_list.append(f"Dynamic EQ ({desc_str})")

    # 3. Clarity
    cla_elem = root.find("Clarity")
    cla_path = os.path.join(vst_dir, "Ozone 12 Clarity.vst3")
    if cla_elem is not None and cla_elem.attrib.get("Enabled") == "1" and os.path.exists(cla_path):
        cla = pedalboard.load_plugin(cla_path)
        for p in cla_elem.findall("Param"):
            pid = p.attrib.get("ParamID")
            val = float(p.attrib.get("Value", 0))
            if pid == "Amount": cla.cla_stereo_main_amount = val
            elif pid == "Attack": cla.cla_stereo_main_attack_ms = val
            elif pid == "Tilt": cla.cla_stereo_main_tilt_db_oct = val
            elif pid == "Release": cla.cla_stereo_main_release_ms = val
        desc_str = f"Amount={cla.cla_stereo_main_amount:.1f}, Tilt={cla.cla_stereo_main_tilt_db_oct:.2f}, Attack={cla.cla_stereo_main_attack_ms:.0f}ms, Release={cla.cla_stereo_main_release_ms:.0f}ms"
        chain_modules.append(("Ozone 12 Clarity", cla, "清晰度增强 (Clarity)", desc_str))
        summary_list.append(f"Clarity (Amount={cla.cla_stereo_main_amount:.1f}, Tilt={cla.cla_stereo_main_tilt_db_oct:.2f})")

    # 4. Equalizer
    eq_elem = root.find("EQ")
    eq_path = os.path.join(vst_dir, "Ozone 12 Equalizer.vst3")
    if eq_elem is not None and eq_elem.attrib.get("Enabled") == "1":
        eq_sub_plugins = []
        if os.path.exists(eq_path):
            eq_vst = pedalboard.load_plugin(eq_path)
            eq_sub_plugins.append(eq_vst)
        bands = {}
        for p in eq_elem.findall("Param"):
            pid = p.attrib.get("ParamID", "")
            val = float(p.attrib.get("Value", 0))
            for b_idx in range(1, 9):
                prefix = f"Band {b_idx} "
                if pid.startswith(prefix):
                    bands.setdefault(b_idx, {})[pid[len(prefix):]] = val
        band_descs = []
        for b_idx, b_data in sorted(bands.items()):
            if b_data.get("Enable", 1) and "Frequency" in b_data and "Gain" in b_data:
                f = b_data["Frequency"]
                g = b_data["Gain"]
                q = b_data.get("Q", 0.7)
                if abs(g) > 0.05:
                    if f < 40:
                        eq_sub_plugins.append(pedalboard.LowShelfFilter(cutoff_frequency_hz=f, gain_db=g))
                    elif f > 8000:
                        eq_sub_plugins.append(pedalboard.HighShelfFilter(cutoff_frequency_hz=f, gain_db=g))
                    else:
                        eq_sub_plugins.append(pedalboard.PeakFilter(cutoff_frequency_hz=f, gain_db=g, q=q))
                band_descs.append(f"{f:.0f}Hz({g:+.1f}dB)")
        eq_processor = pedalboard.Pedalboard(eq_sub_plugins)
        desc_str = f"{len(band_descs)} 个EQ频段: {', '.join(band_descs)}"
        chain_modules.append(("Ozone 12 Equalizer", eq_processor, "母带精细均衡 (Equalizer)", desc_str))
        summary_list.append(f"Equalizer ({desc_str})")

    # 5. Low End Focus
    lef_elem = root.find("LowEndFocus")
    lef_path = os.path.join(vst_dir, "Ozone 12 Low End Focus.vst3")
    if lef_elem is not None and lef_elem.attrib.get("Enabled") == "1" and os.path.exists(lef_path):
        lef = pedalboard.load_plugin(lef_path)
        for p in lef_elem.findall("Param"):
            pid = p.attrib.get("ParamID")
            val = float(p.attrib.get("Value", 0))
            if pid == "Gain": lef.lef_stereo_main_gain_db = val
            elif pid == "Strength": lef.lef_stereo_main_contrast = val
        desc_str = f"Gain={lef.lef_stereo_main_gain_db:+.2f}dB, Strength={lef.lef_stereo_main_contrast:.1f}"
        chain_modules.append(("Ozone 12 Low End Focus", lef, "低频对焦 (Low End Focus)", desc_str))
        summary_list.append(f"Low End Focus ({desc_str})")

    # 6. Maximizer
    max_elem = root.find("Maximizer")
    max_path = os.path.join(vst_dir, "Ozone 12 Maximizer.vst3")
    if max_elem is not None and max_elem.attrib.get("Enabled") == "1" and os.path.exists(max_path):
        maxi = pedalboard.load_plugin(max_path)
        maxi.max_character = 2.0  # IRC IV 模式
        maxi.max_output_level = -0.3  # True-Peak 天花板
        maxi.max_input_gain_db = 2.0  # 商业母带驱动增益
        desc_str = "IRC IV 智能透明防削波, Ceiling -0.3dBFS, Drive +2.0dB"
        chain_modules.append(("Ozone 12 Maximizer", maxi, "商业最大化限制器 (Maximizer)", desc_str))
        summary_list.append(f"Maximizer ({desc_str})")

    return chain_modules, summary_list


# ==============================================================================
# 配置持久化辅助函数
# ==============================================================================
def load_config() -> dict:
    default_config = {
        "active_tab": 0,  # 0: Matchering, 1: Ozone 12
        "output_format": DEFAULT_AUDIO_FORMAT,
        "loudness_level": 3,
        "filename_suffix": "_mastered",
        "use_limiter": True,
        "normalize": True,
        "output_dir": os.path.abspath(os.path.join(os.getcwd(), "output_mastered")),
        "embed_cover": True,
        "ozone_vst_dir": OZONE_VST3_DIR_DEFAULT,
        "ozone_preset_path": OZONE_KS_XML_DEFAULT,
        "metadata": {
            "artist": "",
            "year": "",
            "album": "",
            "genre": ""
        }
    }
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
                default_config.update(saved)
                if "metadata" in saved:
                    default_config["metadata"].update(saved["metadata"])
                if default_config.get("output_format") == "FLAC_24":
                    default_config["output_format"] = DEFAULT_AUDIO_FORMAT
        except Exception as e:
            print("读取配置文件失败，使用默认配置:", e)
    return default_config


def save_config(cfg: dict) -> None:
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("保存配置文件失败:", e)


# ==============================================================================
# 封面查找、压缩与标签写入
# ==============================================================================
def find_cover_image_in_directory(target_audio_path: str) -> str | None:
    """在目标音乐所在文件夹下查找封面图片，第一优先级匹配同名图片（如 歌曲名.jpg）"""
    dir_path = os.path.dirname(os.path.abspath(target_audio_path))
    if not os.path.isdir(dir_path):
        return None

    target_stem = Path(target_audio_path).stem.strip().lower()
    img_exts = [".jpg", ".jpeg", ".png", ".webp", ".bmp"]

    try:
        entries = os.listdir(dir_path)
    except Exception:
        return None

    # 1. 绝对第一优先级：与音乐文件同名的图片（如 晴天.wav 对应同目录下的 晴天.jpg）
    for f in entries:
        stem, ext = os.path.splitext(f)
        if ext.lower() in img_exts and stem.strip().lower() == target_stem:
            return os.path.join(dir_path, f)

    # 2. 第二优先级：常用封面命名（cover, folder, front, album, artwork, 封面等）
    common_names = [
        "cover", "folder", "front", "album", "artwork",
        "封面", "front_cover", "cd", "disc"
    ]
    for name in common_names:
        for ext in img_exts:
            for case_ext in (ext, ext.upper()):
                candidate = os.path.join(dir_path, f"{name}{case_ext}")
                if os.path.isfile(candidate):
                    return candidate
                candidate_up = os.path.join(dir_path, f"{name.upper()}{case_ext}")
                if os.path.isfile(candidate_up):
                    return candidate_up

    # 3. 第三优先级：文件名中包含 cover / front / 封面 关键词的文件
    for f in entries:
        stem, ext = os.path.splitext(f)
        if ext.lower() in img_exts:
            low_stem = stem.lower()
            if any(k in low_stem for k in ["cover", "folder", "front", "封面"]):
                return os.path.join(dir_path, f)

    # 4. 第四优先级：若无特殊命名，挑目录中最大的一张图片
    all_imgs = [
        os.path.join(dir_path, f)
        for f in entries
        if os.path.splitext(f)[1].lower() in img_exts
    ]
    if all_imgs:
        all_imgs.sort(key=lambda x: os.path.getsize(x) if os.path.exists(x) else 0, reverse=True)
        return all_imgs[0]

    return None


def extract_source_tags_and_cover(source_path: str) -> tuple[dict, bytes | None, str]:
    """
    深入解析源音频文件，提取内嵌的元数据标签与内嵌封面图片。
    返回: (tags_dict, cover_bytes, cover_mime)
    """
    tags_extracted = {}
    cover_bytes = None
    cover_mime = "image/jpeg"

    if not source_path or not os.path.isfile(source_path):
        return tags_extracted, cover_bytes, cover_mime

    try:
        import mutagen
        mf = mutagen.File(source_path)
        if mf is not None:
            # 1. 提取内嵌封面图片
            # 1.1 FLAC / OGG 结构
            if hasattr(mf, "pictures") and mf.pictures:
                front_pics = [p for p in mf.pictures if getattr(p, "type", None) == 3]
                pic = front_pics[0] if front_pics else mf.pictures[0]
                cover_bytes = pic.data
                cover_mime = getattr(pic, "mime", "image/jpeg") or "image/jpeg"

            # 1.2 ID3 结构 (MP3 / WAV / AIFF)
            elif hasattr(mf, "tags") and mf.tags is not None:
                if hasattr(mf.tags, "getall"):
                    apics = mf.tags.getall("APIC")
                    if apics:
                        front_apics = [p for p in apics if getattr(p, "type", None) == 3]
                        pic = front_apics[0] if front_apics else apics[0]
                        cover_bytes = pic.data
                        cover_mime = getattr(pic, "mime", "image/jpeg") or "image/jpeg"
                if cover_bytes is None:
                    for k, v in mf.tags.items():
                        if k.startswith("APIC"):
                            cover_bytes = v.data
                            cover_mime = getattr(v, "mime", "image/jpeg") or "image/jpeg"
                            break
                        elif k == "covr" and isinstance(v, list) and v:
                            cover_bytes = bytes(v[0])
                            if cover_bytes.startswith(b"\x89PNG"):
                                cover_mime = "image/png"
                            else:
                                cover_mime = "image/jpeg"
                            break

            # 2. 提取文本标签
            if hasattr(mf, "tags") and mf.tags:
                tags = mf.tags

                def _get_tag_val(*keys):
                    for k in keys:
                        try:
                            if k in tags:
                                v = tags[k]
                                if isinstance(v, list) and v:
                                    return str(v[0]).strip()
                                elif hasattr(v, "text") and v.text:
                                    return str(v.text[0]).strip()
                                elif isinstance(v, (str, int, float)):
                                    return str(v).strip()
                        except Exception:
                            pass
                        try:
                            for existing_k in tags.keys():
                                if str(existing_k).lower() == k.lower():
                                    v = tags[existing_k]
                                    if isinstance(v, list) and v:
                                        return str(v[0]).strip()
                                    elif hasattr(v, "text") and v.text:
                                        return str(v.text[0]).strip()
                                    elif isinstance(v, (str, int, float)):
                                        return str(v).strip()
                        except Exception:
                            pass
                    return ""

                title = _get_tag_val("title", "TIT2", "\xa9nam")
                artist = _get_tag_val("artist", "TPE1", "\xa9ART")
                album = _get_tag_val("album", "TALB", "\xa9alb")
                year = _get_tag_val("date", "year", "TDRC", "TYER", "\xa9day")
                genre = _get_tag_val("genre", "TCON", "\xa9gen")
                track = _get_tag_val("tracknumber", "track", "TRCK", "trkn")
                albumartist = _get_tag_val("albumartist", "album_artist", "TPE2", "aART")
                comment = _get_tag_val("comment", "COMM", "\xa9cmt", "description")

                if title: tags_extracted["title"] = title
                if artist: tags_extracted["artist"] = artist
                if album: tags_extracted["album"] = album
                if year:
                    y_str = str(year)
                    tags_extracted["year"] = y_str[:4] if len(y_str) >= 4 and y_str[:4].isdigit() else y_str
                if genre: tags_extracted["genre"] = genre
                if track: tags_extracted["track"] = track
                if albumartist: tags_extracted["albumartist"] = albumartist
                if comment: tags_extracted["comment"] = comment

    except Exception as e:
        print(f"提取源音频元数据异常: {e}")

    return tags_extracted, cover_bytes, cover_mime


def apply_metadata_and_cover(
    file_path: str,
    target_original_path: str,
    metadata: dict,
    embed_cover: bool = True
) -> dict:
    """向生成的音频文件（FLAC / MP3 / WAV）内嵌元数据和封面图片，优先保持源文件内嵌封面与元数据"""
    result_info = {
        "cover_embedded": False,
        "cover_size": 0,
        "cover_path": "",
        "cover_source": "",
        "is_same_name": False
    }
    ext = Path(file_path).suffix.lower()

    # 0. 预先从源音频提取已有内嵌标签与封面
    src_tags, src_cover_bytes, src_cover_mime = extract_source_tags_and_cover(target_original_path)

    # 1. 查找并压缩封面（同名本地图片 > 源文件内嵌封面 > 本地目录图片）
    cover_bytes = None
    cover_mime = "image/jpeg"
    if embed_cover:
        dir_cover_path = find_cover_image_in_directory(target_original_path)
        target_stem = Path(target_original_path).stem.strip().lower()

        # 优先级 1: 本地文件夹中的同名图片（如 晴天.jpg）
        if dir_cover_path and os.path.exists(dir_cover_path):
            dir_stem = Path(dir_cover_path).stem.strip().lower()
            if dir_stem == target_stem:
                try:
                    cover_bytes = compress_cover_image(dir_cover_path)
                    cover_mime = "image/jpeg"
                    result_info["cover_path"] = dir_cover_path
                    result_info["is_same_name"] = True
                    result_info["cover_source"] = "同名本地封面"
                except Exception as e:
                    print(f"同名封面压缩失败: {e}")

        # 优先级 2: 源音频文件内嵌的原始封面
        if cover_bytes is None and src_cover_bytes is not None:
            cover_bytes = src_cover_bytes
            cover_mime = src_cover_mime
            result_info["cover_path"] = "源文件内嵌封面"
            result_info["cover_source"] = "源文件内嵌封面"

        # 优先级 3: 本地文件夹中通用命名封面（如 cover.jpg / folder.jpg 等）
        if cover_bytes is None and dir_cover_path and os.path.exists(dir_cover_path):
            try:
                cover_bytes = compress_cover_image(dir_cover_path)
                cover_mime = "image/jpeg"
                result_info["cover_path"] = dir_cover_path
                result_info["cover_source"] = "目录本地封面"
            except Exception as e:
                print(f"备选目录封面压缩失败: {e}")

        if cover_bytes:
            result_info["cover_embedded"] = True
            result_info["cover_size"] = len(cover_bytes)

    # 2. 合并元数据：源文件元数据为基底，界面非空输入作为优先覆盖
    final_meta = {}
    final_meta["title"] = src_tags.get("title", Path(target_original_path).stem)
    final_meta["artist"] = metadata.get("artist") or src_tags.get("artist", "")
    final_meta["album"] = metadata.get("album") or src_tags.get("album", "")
    final_meta["year"] = metadata.get("year") or src_tags.get("year", "")
    final_meta["genre"] = metadata.get("genre") or src_tags.get("genre", "")
    final_meta["track"] = src_tags.get("track", "")
    final_meta["albumartist"] = src_tags.get("albumartist", "")

    # 3. 写入 FLAC 元数据
    if ext == ".flac":
        try:
            flac = FLAC(file_path)
            if final_meta["title"]: flac["title"] = final_meta["title"]
            if final_meta["artist"]: flac["artist"] = final_meta["artist"]
            if final_meta["album"]: flac["album"] = final_meta["album"]
            if final_meta["year"]: flac["date"] = final_meta["year"]
            if final_meta["genre"]: flac["genre"] = final_meta["genre"]
            if final_meta["track"]: flac["tracknumber"] = final_meta["track"]
            if final_meta["albumartist"]: flac["albumartist"] = final_meta["albumartist"]

            if cover_bytes:
                pic = Picture()
                pic.type = 3  # Front Cover
                pic.mime = cover_mime
                pic.desc = "Front Cover"
                pic.data = cover_bytes
                flac.clear_pictures()
                flac.add_picture(pic)

            flac.save()
        except Exception as e:
            print(f"FLAC 标签写入失败: {e}")

    # 4. 写入 MP3 ID3v2.3 元数据
    elif ext == ".mp3":
        try:
            try:
                mp3 = MP3(file_path)
            except Exception:
                mp3 = MP3(file_path)
            if mp3.tags is None:
                mp3.add_tags()
            tags = mp3.tags
            if final_meta["title"]: tags.add(TIT2(encoding=3, text=final_meta["title"]))
            if final_meta["artist"]: tags.add(TPE1(encoding=3, text=final_meta["artist"]))
            if final_meta["album"]: tags.add(TALB(encoding=3, text=final_meta["album"]))
            if final_meta["year"]: tags.add(TDRC(encoding=3, text=str(final_meta["year"])))
            if final_meta["genre"]: tags.add(TCON(encoding=3, text=final_meta["genre"]))
            if final_meta["track"]: tags.add(TRCK(encoding=3, text=str(final_meta["track"])))
            if final_meta["albumartist"]: tags.add(TPE2(encoding=3, text=final_meta["albumartist"]))

            if cover_bytes:
                tags.delall("APIC")
                tags.add(APIC(
                    encoding=3,
                    mime=cover_mime,
                    type=3,  # Front Cover
                    desc="Cover",
                    data=cover_bytes
                ))
            tags.save(file_path, v2_version=3)
        except Exception as e:
            print(f"MP3 标签写入失败: {e}")

    # 5. 写入 WAV ID3 元数据
    elif ext == ".wav":
        try:
            try:
                wav_id3 = ID3(file_path)
            except Exception:
                wav_id3 = ID3()

            if final_meta["title"]: wav_id3.add(TIT2(encoding=3, text=final_meta["title"]))
            if final_meta["artist"]: wav_id3.add(TPE1(encoding=3, text=final_meta["artist"]))
            if final_meta["album"]: wav_id3.add(TALB(encoding=3, text=final_meta["album"]))
            if final_meta["year"]: wav_id3.add(TDRC(encoding=3, text=str(final_meta["year"])))
            if final_meta["genre"]: wav_id3.add(TCON(encoding=3, text=final_meta["genre"]))
            if final_meta["track"]: wav_id3.add(TRCK(encoding=3, text=str(final_meta["track"])))
            if final_meta["albumartist"]: wav_id3.add(TPE2(encoding=3, text=final_meta["albumartist"]))

            if cover_bytes:
                wav_id3.delall("APIC")
                wav_id3.add(APIC(
                    encoding=3,
                    mime=cover_mime,
                    type=3,
                    desc="Cover",
                    data=cover_bytes
                ))
            wav_id3.save(file_path)
        except Exception as e:
            print(f"WAV 标签写入失败: {e}")

    return result_info


def apply_loudness_gain(file_path: str, gain_db: float, subtype: str):
    """应用线性增益调整（衰减）"""
    if np.isclose(gain_db, 0.0):
        return

    gain = 10.0 ** (gain_db / 20.0)
    data, sr = sf.read(file_path, always_2d=True)
    data = data * gain
    sf.write(file_path, data, sr, subtype)


def get_audio_info_str(path: str) -> str:
    """获取音频文件的基本规格描述"""
    try:
        info = sf.info(path)
        mins = int(info.duration // 60)
        secs = int(info.duration % 60)
        dur_str = f"{mins:02d}:{secs:02d}"
        sr_khz = info.samplerate / 1000
        sr_str = f"{sr_khz:.1f}kHz" if sr_khz % 1 != 0 else f"{int(sr_khz)}kHz"
        ch_str = "立体声" if info.channels == 2 else ("单声道" if info.channels == 1 else f"{info.channels}声道")
        subtype = info.subtype.replace("PCM_", "") if info.subtype else ""
        return f"{dur_str} | {sr_str} | {ch_str} {subtype}".strip()
    except Exception:
        return "音频文件"


def format_size(bytes_val: int) -> str:
    val = float(bytes_val)
    for unit in ['B', 'KB', 'MB', 'GB']:
        if val < 1024.0:
            return f"{val:.1f} {unit}"
        val /= 1024.0
    return f"{val:.1f} GB"


# ==============================================================================
# 工作线程：支持 Matchering 参考母带 与 Ozone 12 KS 预设两种引擎
# ==============================================================================
class MasteringWorker(QThread):
    task_started = pyqtSignal(int, str)
    task_progress = pyqtSignal(int, int, str)
    task_finished = pyqtSignal(int, bool, str, float, str)
    batch_progress = pyqtSignal(int, int)
    batch_finished = pyqtSignal(int, int, float)
    log_emitted = pyqtSignal(str, str)

    def __init__(
        self,
        engine_mode: str,  # "matchering" 或 "ozone12"
        target_tasks: list,
        output_dir: str,
        reference_path: str = "",
        ozone_preset_path: str = OZONE_KS_XML_DEFAULT,
        ozone_vst_dir: str = OZONE_VST3_DIR_DEFAULT,
        format_code: str = DEFAULT_AUDIO_FORMAT,
        loudness_db: float = -3.0,
        filename_suffix: str = "_mastered",
        use_limiter: bool = True,
        normalize: bool = True,
        metadata: dict = None,
        embed_cover: bool = True,
        ozone_chain_modules: list = None,
        ozone_summary_list: list = None,
        parent=None
    ):
        super().__init__(parent)
        self.engine_mode = engine_mode
        self.target_tasks = target_tasks
        self.output_dir = output_dir
        self.reference_path = reference_path
        self.ozone_preset_path = ozone_preset_path
        self.ozone_vst_dir = ozone_vst_dir
        self.format_code = format_code
        self.loudness_db = loudness_db
        self.filename_suffix = filename_suffix
        self.use_limiter = use_limiter
        self.normalize = normalize
        self.metadata = metadata or {}
        self.embed_cover = embed_cover
        self.ozone_chain_modules = ozone_chain_modules
        self.ozone_summary_list = ozone_summary_list
        self._is_cancelled = False
        self._current_row_idx = -1

    def cancel(self):
        self._is_cancelled = True

    def run(self):
        total_count = len(self.target_tasks)
        success_count = 0
        failed_count = 0
        start_time_total = time.time()

        os.makedirs(self.output_dir, exist_ok=True)

        fmt_info = AUDIO_FORMATS.get(self.format_code, AUDIO_FORMATS[DEFAULT_AUDIO_FORMAT])
        ext, subtype, target_sr, format_display = fmt_info

        if self.engine_mode == "passthrough":
            self._run_passthrough(total_count, ext, subtype, target_sr, format_display, start_time_total)
        elif self.engine_mode == "ozone12":
            self._run_ozone12(total_count, ext, subtype, target_sr, format_display, start_time_total)
        else:
            self._run_matchering(total_count, ext, subtype, target_sr, format_display, start_time_total)

    def _run_passthrough(self, total_count, ext, subtype, target_sr, format_display, start_time_total):
        """执行原曲直通批量格式转换（不做任何 DSP / 压限 / 增益修改，保持波形原汁原味，继承封面与元数据）"""
        success_count = 0
        failed_count = 0

        self.log_emitted.emit("SYSTEM", f"【原曲直通模式】开始批量格式转换，共 {total_count} 首音频...")
        self.log_emitted.emit("SYSTEM", f"目标输出格式: {format_display} (原曲直通：零音频渲染损耗，不修改音频动态与响度)")

        for i, (row_idx, target_path) in enumerate(self.target_tasks):
            if self._is_cancelled:
                self.log_emitted.emit("WARN", "用户已中止后续任务处理。")
                break

            self._current_row_idx = row_idx
            target_name = os.path.basename(target_path)
            self.task_started.emit(row_idx, target_name)
            self.log_emitted.emit("TASK", f"\n[{i+1}/{total_count}] 开始转换: {target_name}")

            task_start_time = time.time()
            base_name, src_ext = os.path.splitext(target_name)

            # 原曲直通模式下：默认保持与原文件同名，仅改变扩展名（如 .mp3）
            # 若仍带有母带模式默认的 "_mastered" / "-Mastered"，在直通模式下自动去除
            suffix = self.filename_suffix
            if suffix in ("_mastered", "-Mastered"):
                suffix = ""

            output_filename = f"{base_name}{suffix}{ext}"
            output_filepath = os.path.join(self.output_dir, output_filename)

            # 防覆盖保护：若输出目录与源文件相同且同扩展名，避免直接覆盖源文件
            if os.path.abspath(output_filepath) == os.path.abspath(target_path):
                output_filename = f"{base_name}_converted{ext}"
                output_filepath = os.path.join(self.output_dir, output_filename)
                self.log_emitted.emit("WARN", f"⚠️ 检测到输出路径与源文件相同，为避免覆盖原曲，已自动重命名为: {output_filename}")


            try:
                # 1. 读取音频数据
                self.task_progress.emit(self._current_row_idx, 20, "正在读取音频数据...")
                audio_data, sr = sf.read(target_path, always_2d=True, dtype="float32")
                if audio_data.shape[1] == 1:
                    audio_data = np.column_stack((audio_data, audio_data))

                # 2. 如果目标格式指定了固定采样率（如 96kHz FLAC），进行高保真重采样；否则保持原始采样率
                out_sr = sr
                if target_sr is not None and sr != target_sr:
                    self.task_progress.emit(self._current_row_idx, 50, f"正在高质量重采样至 {target_sr // 1000} kHz...")
                    audio_data = resample_audio(audio_data, sr, target_sr)
                    out_sr = target_sr
                    self.log_emitted.emit("INFO", f"  ↳ 采样率转换: {sr} Hz ➔ {target_sr} Hz (高阶多相滤波重采样)")

                # 3. 编码保存为目标格式
                self.task_progress.emit(self._current_row_idx, 80, f"正在编码导出为 {format_display}...")
                save_audio_file(output_filepath, audio_data, out_sr, self.format_code)

                # 4. 提取与内嵌封面与元数据
                self.task_progress.emit(self._current_row_idx, 95, "正在继承内嵌封面与元数据标签...")
                tag_res = apply_metadata_and_cover(
                    output_filepath, target_path, self.metadata, self.embed_cover
                )
                if tag_res["cover_embedded"]:
                    cover_src = tag_res.get("cover_source", "封面")
                    cover_name = os.path.basename(tag_res["cover_path"]) if tag_res["cover_path"] != "源文件内嵌封面" else "源文件内嵌封面"
                    size_kb = tag_res["cover_size"] / 1024.0
                    self.log_emitted.emit("SUCCESS", f"🖼️ 成功内嵌[{cover_src}]: {cover_name} (大小 {size_kb:.1f} KB)")
                else:
                    self.log_emitted.emit("DEBUG", "未发现封面图片，已跳过封面内嵌。")

                elapsed = time.time() - task_start_time
                success_count += 1
                self.task_finished.emit(row_idx, True, output_filepath, elapsed, "完成")
                self.log_emitted.emit("SUCCESS", f"✅ 转换完成: {output_filename} (耗时: {elapsed:.2f}s)")

            except Exception as e:
                elapsed = time.time() - task_start_time
                failed_count += 1
                err_msg = str(e)
                self.task_finished.emit(row_idx, False, "", elapsed, f"错误: {err_msg}")
                self.log_emitted.emit("ERROR", f"❌ 转换失败: {target_name} -> {err_msg}")
                self.log_emitted.emit("DEBUG", traceback.format_exc())

            self.batch_progress.emit(i + 1, total_count)

        total_elapsed = time.time() - start_time_total
        self.batch_finished.emit(success_count, failed_count, total_elapsed)

    def _run_ozone12(self, total_count, ext, subtype, target_sr, format_display, start_time_total):
        """执行 iZotope Ozone 12 预设母带链批量离线渲染"""
        success_count = 0
        failed_count = 0

        self.log_emitted.emit("SYSTEM", f"【Ozone 12 模式】开始执行批量增强处理，共 {total_count} 首音频...")
        self.log_emitted.emit("SYSTEM", f"输出格式: {format_display} | 响度微调: {self.loudness_db:+.1f} dB")
        if self.metadata:
            meta_summary = ", ".join(f"{k}={v}" for k, v in self.metadata.items() if v)
            if meta_summary:
                self.log_emitted.emit("SYSTEM", f"全局元数据: {meta_summary}")

        # 基于 XML 预设使用或动态装配 iZotope 原生独立 VST3 模块链
        if self.ozone_chain_modules:
            chain_modules = self.ozone_chain_modules
            summary_list = self.ozone_summary_list or []
            self.log_emitted.emit("SUCCESS", f"✅ 已成功复用主线程装配的 Ozone 12 原生母带流水线 (共 {len(chain_modules)} 个核心模块):")
            for s in summary_list:
                self.log_emitted.emit("SUCCESS", f"   ↳ {s}")
        else:
            self.log_emitted.emit("INFO", f"正在读取 Ozone 预设文件: {self.ozone_preset_path} ...")
            try:
                chain_modules, summary_list = create_ozone_chain_from_xml(self.ozone_preset_path, self.ozone_vst_dir)
                self.log_emitted.emit("SUCCESS", f"✅ 成功装配 Ozone 12 原生母带流水线 (共 {len(chain_modules)} 个核心模块):")
                for s in summary_list:
                    self.log_emitted.emit("SUCCESS", f"   ↳ {s}")
            except Exception as e:
                self.log_emitted.emit("ERROR", f"❌ 加载 Ozone 12 预设母带链失败: {e}")
                self.log_emitted.emit("DEBUG", traceback.format_exc())
                self.batch_finished.emit(0, total_count, 0)
                return

        for i, (row_idx, target_path) in enumerate(self.target_tasks):
            if self._is_cancelled:
                self.log_emitted.emit("WARN", "用户已中止后续任务处理。")
                break

            self._current_row_idx = row_idx
            target_name = os.path.basename(target_path)
            self.task_started.emit(row_idx, target_name)
            self.log_emitted.emit("TASK", f"\n[{i+1}/{total_count}] 开始处理: {target_name}")

            task_start_time = time.time()
            base_name, _ = os.path.splitext(target_name)
            output_filename = f"{base_name}{self.filename_suffix}{ext}"
            output_filepath = os.path.join(self.output_dir, output_filename)

            try:
                # 1. 读取音频数据
                self.task_progress.emit(self._current_row_idx, 15, "正在读取音频数据...")
                audio_data, sr = sf.read(target_path, always_2d=True, dtype="float32")
                if audio_data.shape[1] == 1:
                    audio_data = np.column_stack((audio_data, audio_data))

                # 转置为 (channels, samples) 格式供 pedalboard 处理
                audio_in = audio_data.T

                proc_sr = sr
                if target_sr is not None and sr != target_sr:
                    self.task_progress.emit(self._current_row_idx, 18, f"正在高清升频重采样至 {target_sr // 1000} kHz...")
                    current_audio = resample_audio(audio_in, sr, target_sr)
                    proc_sr = target_sr
                    self.log_emitted.emit("INFO", f"  ↳ 采样率转换: {sr} Hz ➔ {target_sr} Hz (多相滤波高清重采样)")
                else:
                    current_audio = audio_in.copy()

                # 2. 逐级执行 Ozone 12 原生模块流水线 DSP 离线母带处理
                total_mods = len(chain_modules)
                self.log_emitted.emit("INFO", f"  ⚙️ 开始执行 Ozone 12 逐级母带流水线 (共 {total_mods} 级插件, 渲染采样率: {proc_sr} Hz)...")

                for step_idx, (mod_name, proc, mod_title, mod_desc) in enumerate(chain_modules, 1):
                    if self._is_cancelled:
                        raise CancelledException("用户取消了任务")

                    pct = 20 + int(60 * (step_idx / total_mods))
                    self.task_progress.emit(self._current_row_idx, pct, f"[{step_idx}/{total_mods}] {mod_title} 渲染中...")

                    in_peak = float(np.max(np.abs(current_audio)))
                    in_rms = float(np.sqrt(np.mean(current_audio ** 2)))

                    try:
                        stage_audio = proc(current_audio, sample_rate=proc_sr, reset=True)
                    except TypeError:
                        stage_audio = proc(current_audio, sample_rate=proc_sr)

                    out_peak = float(np.max(np.abs(stage_audio)))
                    out_rms = float(np.sqrt(np.mean(stage_audio ** 2)))
                    min_len = min(stage_audio.shape[1], current_audio.shape[1])
                    stage_diff = float(np.max(np.abs(stage_audio[:, :min_len] - current_audio[:, :min_len])))

                    in_peak_db = 20 * np.log10(max(in_peak, 1e-6))
                    out_peak_db = 20 * np.log10(max(out_peak, 1e-6))
                    delta_rms_db = 20 * np.log10(max(out_rms, 1e-6)) - 20 * np.log10(max(in_rms, 1e-6))

                    self.log_emitted.emit(
                        "SUCCESS",
                        f"  ↳ [{step_idx}/{total_mods}] {mod_title} 完成: "
                        f"差异量={stage_diff:.4f}, 电平增益={delta_rms_db:+.2f}dB, 峰值: {in_peak_db:.1f}dB ➔ {out_peak_db:.1f}dB"
                    )
                    current_audio = stage_audio

                audio_out = current_audio

                # 3. 应用响度增益衰减
                if not np.isclose(self.loudness_db, 0.0):
                    self.task_progress.emit(self._current_row_idx, 85, f"应用响度微调 ({self.loudness_db:+.1f} dB)...")
                    gain = 10.0 ** (self.loudness_db / 20.0)
                    audio_out = audio_out * gain
                    self.log_emitted.emit("INFO", f"  ↳ 响度微调: 衰减增益 {self.loudness_db:+.1f} dB")

                # 4. 保存为目标格式
                self.task_progress.emit(self._current_row_idx, 90, f"正在导出 {format_display}...")
                save_audio_file(output_filepath, audio_out, proc_sr, self.format_code)

                # 5. 查找并内嵌封面与标签
                if ext in (".flac", ".wav", ".mp3"):
                    self.task_progress.emit(self._current_row_idx, 96, "正在内嵌封面与写入标签...")
                    tag_res = apply_metadata_and_cover(
                        output_filepath, target_path, self.metadata, self.embed_cover
                    )
                    if tag_res["cover_embedded"]:
                        cover_src = tag_res.get("cover_source", "封面")
                        cover_name = os.path.basename(tag_res["cover_path"]) if tag_res["cover_path"] != "源文件内嵌封面" else "源文件内嵌封面"
                        size_kb = tag_res["cover_size"] / 1024.0
                        self.log_emitted.emit("SUCCESS", f"🖼️ 成功内嵌[{cover_src}]: {cover_name} (大小 {size_kb:.1f} KB)")
                    else:
                        self.log_emitted.emit("DEBUG", "未找到封面图片，跳过封面内嵌。")

                elapsed = time.time() - task_start_time
                success_count += 1
                self.task_finished.emit(row_idx, True, output_filepath, elapsed, "完成")
                self.log_emitted.emit("SUCCESS", f"✅ 处理完成: {output_filename} (耗时: {elapsed:.2f}s)")

            except Exception as e:
                elapsed = time.time() - task_start_time
                failed_count += 1
                err_msg = str(e)
                self.task_finished.emit(row_idx, False, "", elapsed, f"错误: {err_msg}")
                self.log_emitted.emit("ERROR", f"❌ 处理失败: {target_name} -> {err_msg}")
                self.log_emitted.emit("DEBUG", traceback.format_exc())

            self.batch_progress.emit(i + 1, total_count)

        total_elapsed = time.time() - start_time_total
        self.batch_finished.emit(success_count, failed_count, total_elapsed)

    def _run_matchering(self, total_count, ext, subtype, target_sr, format_display, start_time_total):
        """执行 Matchering 参考匹配批量母带"""
        success_count = 0
        failed_count = 0

        def info_handler(msg):
            if self._is_cancelled:
                raise CancelledException("用户手动取消了任务")
            pct = 10
            stage_name = msg
            if "Loading" in msg:
                pct, stage_name = 15, "正在加载与分析音频..."
            elif "Matching levels" in msg:
                pct, stage_name = 35, "正在匹配电平与响度 (RMS)..."
            elif "Matching frequencies" in msg:
                pct, stage_name = 55, "正在匹配频响频谱曲线 (EQ)..."
            elif "Correcting levels" in msg:
                pct, stage_name = 75, "正在校正并微调电平..."
            elif "Final processing" in msg:
                pct, stage_name = 85, "正在应用限制器压限处理..."
            elif "Exporting" in msg:
                pct, stage_name = 92, f"正在导出 {format_display}..."
            elif "completed" in msg:
                pct, stage_name = 98, "算法处理完成"

            self.task_progress.emit(self._current_row_idx, pct, stage_name)
            self.log_emitted.emit("INFO", f"[{stage_name}] {msg}")

        def warning_handler(msg):
            if self._is_cancelled:
                raise CancelledException("用户手动取消了任务")
            self.log_emitted.emit("WARN", f"⚠️ 警告: {msg}")

        def debug_handler(msg):
            if not msg.startswith("-"):
                self.log_emitted.emit("DEBUG", f"  ↳ {msg}")

        mg.log(
            info_handler=info_handler,
            warning_handler=warning_handler,
            debug_handler=debug_handler,
            show_codes=False
        )

        self.log_emitted.emit("SYSTEM", f"【Matchering 模式】开始执行批量参考母带，共 {total_count} 首音频...")
        self.log_emitted.emit("SYSTEM", f"参考歌曲: {self.reference_path}")
        self.log_emitted.emit("SYSTEM", f"输出格式: {format_display} | 响度调节: {self.loudness_db:+.1f} dB")

        for i, (row_idx, target_path) in enumerate(self.target_tasks):
            if self._is_cancelled:
                self.log_emitted.emit("WARN", "用户已中止后续任务处理。")
                break

            self._current_row_idx = row_idx
            target_name = os.path.basename(target_path)
            self.task_started.emit(row_idx, target_name)
            self.log_emitted.emit("TASK", f"\n[{i+1}/{total_count}] 开始处理: {target_name}")

            task_start_time = time.time()
            base_name, _ = os.path.splitext(target_name)
            output_filename = f"{base_name}{self.filename_suffix}{ext}"
            output_filepath = os.path.join(self.output_dir, output_filename)

            try:
                # 若导出格式为 MP3，Matchering 核心先输出临时 WAV 再转码
                is_mp3 = (ext == ".mp3")
                mg_output = (output_filepath + ".tmp.wav") if is_mp3 else output_filepath
                mg_subtype = "PCM_24" if is_mp3 else subtype

                res_obj = Result(
                    file=mg_output,
                    subtype=mg_subtype,
                    use_limiter=self.use_limiter,
                    normalize=self.normalize
                )

                mg.process(
                    target=target_path,
                    reference=self.reference_path,
                    results=[res_obj]
                )

                # 后处理：按需重采样、响度微调与格式终裁
                needs_resample = False
                needs_gain = not np.isclose(self.loudness_db, 0.0)
                out_data, out_sr = sf.read(mg_output, always_2d=True, dtype="float32")
                if target_sr is not None and out_sr != target_sr:
                    needs_resample = True

                if needs_resample or needs_gain or is_mp3:
                    self.task_progress.emit(self._current_row_idx, 95, "正在进行后处理与重采样...")
                    if needs_resample:
                        out_data = resample_audio(out_data, out_sr, target_sr)
                        out_sr = target_sr
                        self.log_emitted.emit("INFO", f"  ↳ 已高质量重采样输出为 {target_sr} Hz")
                    if needs_gain:
                        gain = 10.0 ** (self.loudness_db / 20.0)
                        out_data = out_data * gain
                        self.log_emitted.emit("INFO", f"  ↳ 已应用输出响度调节: {self.loudness_db:+.1f} dB")
                    save_audio_file(output_filepath, out_data, out_sr, self.format_code)
                    if is_mp3 and os.path.exists(mg_output):
                        try:
                            os.remove(mg_output)
                        except Exception:
                            pass

                if ext in (".flac", ".wav", ".mp3"):
                    self.task_progress.emit(self._current_row_idx, 97, "正在内嵌封面与写入标签...")
                    tag_res = apply_metadata_and_cover(
                        output_filepath, target_path, self.metadata, self.embed_cover
                    )
                    if tag_res["cover_embedded"]:
                        cover_src = tag_res.get("cover_source", "封面")
                        cover_name = os.path.basename(tag_res["cover_path"]) if tag_res["cover_path"] != "源文件内嵌封面" else "源文件内嵌封面"
                        size_kb = tag_res["cover_size"] / 1024.0
                        self.log_emitted.emit("SUCCESS", f"🖼️ 成功内嵌[{cover_src}]: {cover_name} (大小 {size_kb:.1f} KB)")
                    else:
                        self.log_emitted.emit("DEBUG", "未发现封面图片，跳过封面内嵌。")

                elapsed = time.time() - task_start_time
                success_count += 1
                self.task_finished.emit(row_idx, True, output_filepath, elapsed, "完成")
                self.log_emitted.emit("SUCCESS", f"✅ 处理完成: {output_filename} (耗时: {elapsed:.1f}s)")

            except CancelledException:
                elapsed = time.time() - task_start_time
                self.task_finished.emit(row_idx, False, "", elapsed, "已取消")
                self.log_emitted.emit("WARN", f"⏹️ 任务已取消: {target_name}")
                if os.path.exists(output_filepath):
                    try:
                        os.remove(output_filepath)
                    except Exception:
                        pass
                break

            except Exception as e:
                elapsed = time.time() - task_start_time
                failed_count += 1
                err_msg = str(e)
                self.task_finished.emit(row_idx, False, "", elapsed, f"错误: {err_msg}")
                self.log_emitted.emit("ERROR", f"❌ 处理失败: {target_name} -> {err_msg}")
                self.log_emitted.emit("DEBUG", traceback.format_exc())

            self.batch_progress.emit(i + 1, total_count)

        total_elapsed = time.time() - start_time_total
        self.batch_finished.emit(success_count, failed_count, total_elapsed)


# ==============================================================================
# 选项卡 1：参考歌曲卡片 (Matchering)
# ==============================================================================
class DropReferenceWidget(QFrame):
    file_selected = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setObjectName("RefCard")
        self.setStyleSheet("""
            QFrame#RefCard {
                background-color: #202129;
                border: 2px dashed #4b5563;
                border-radius: 8px;
                padding: 10px;
            }
            QFrame#RefCard:hover {
                border-color: #3b82f6;
                background-color: #262934;
            }
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 8, 14, 8)
        layout.setSpacing(12)

        self.icon_label = QLabel("🎵")
        self.icon_label.setStyleSheet("font-size: 26px;")
        layout.addWidget(self.icon_label)

        text_layout = QVBoxLayout()
        text_layout.setSpacing(3)
        self.title_label = QLabel("参考歌曲 (Reference Track) - 决定母带的频响曲线与动态特征")
        self.title_label.setStyleSheet("font-size: 13px; font-weight: bold; color: #e2e8f0;")

        self.path_label = QLabel("尚未选择参考歌曲 (点击右侧【浏览】或直接拖拽音频至此处)")
        self.path_label.setStyleSheet("font-size: 12px; color: #94a3b8;")
        self.path_label.setWordWrap(True)

        self.meta_label = QLabel("")
        self.meta_label.setStyleSheet("font-size: 11px; color: #38bdf8;")

        text_layout.addWidget(self.title_label)
        text_layout.addWidget(self.path_label)
        text_layout.addWidget(self.meta_label)
        layout.addLayout(text_layout, stretch=1)

        self.btn_browse = QPushButton("📁 浏览文件...")
        self.btn_browse.setStyleSheet("""
            QPushButton {
                background-color: #3b82f6;
                color: white;
                font-weight: bold;
                padding: 7px 16px;
                border-radius: 6px;
                font-size: 12px;
            }
            QPushButton:hover { background-color: #2563eb; }
        """)
        self.btn_browse.clicked.connect(self._on_browse_clicked)
        layout.addWidget(self.btn_browse)

        self.btn_clear = QPushButton("✕")
        self.btn_clear.setToolTip("清除参考歌曲")
        self.btn_clear.setStyleSheet("""
            QPushButton {
                background-color: #374151;
                color: #9ca3af;
                padding: 7px 10px;
                border-radius: 6px;
            }
            QPushButton:hover { background-color: #ef4444; color: white; }
        """)
        self.btn_clear.clicked.connect(self.clear)
        self.btn_clear.setVisible(False)
        layout.addWidget(self.btn_clear)

        self.current_file = ""

    def _on_browse_clicked(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "选择参考歌曲 (高质量已母带音频)", "", AUDIO_FILTER_STR
        )
        if file_path:
            self.set_file(file_path)

    def set_file(self, file_path: str):
        self.current_file = file_path
        file_name = os.path.basename(file_path)
        info_str = get_audio_info_str(file_path)
        size_str = format_size(os.path.getsize(file_path)) if os.path.exists(file_path) else ""

        self.path_label.setText(f"{file_name}  ({file_path})")
        self.path_label.setStyleSheet("font-size: 12px; color: #f1f5f9; font-weight: 500;")
        self.meta_label.setText(f"规格: {info_str} | 大小: {size_str}")
        self.setStyleSheet("""
            QFrame#RefCard {
                background-color: #1a2333;
                border: 2px solid #3b82f6;
                border-radius: 8px;
                padding: 10px;
            }
        """)
        self.icon_label.setText("💿")
        self.btn_clear.setVisible(True)
        self.file_selected.emit(file_path)

    def clear(self):
        self.current_file = ""
        self.path_label.setText("尚未选择参考歌曲 (点击右侧【浏览】或直接拖拽音频至此处)")
        self.path_label.setStyleSheet("font-size: 12px; color: #94a3b8;")
        self.meta_label.setText("")
        self.icon_label.setText("🎵")
        self.btn_clear.setVisible(False)
        self.setStyleSheet("""
            QFrame#RefCard {
                background-color: #202129;
                border: 2px dashed #4b5563;
                border-radius: 8px;
                padding: 10px;
            }
            QFrame#RefCard:hover {
                border-color: #3b82f6;
                background-color: #262934;
            }
        """)
        self.file_selected.emit("")

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                ext = Path(url.toLocalFile()).suffix.lower()
                if ext in SUPPORTED_EXTENSIONS:
                    event.acceptProposedAction()
                    return
        event.ignore()

    def dropEvent(self, event: QDropEvent):
        for url in event.mimeData().urls():
            file_path = url.toLocalFile()
            ext = Path(file_path).suffix.lower()
            if ext in SUPPORTED_EXTENSIONS and os.path.isfile(file_path):
                self.set_file(file_path)
                event.acceptProposedAction()
                return


# ==============================================================================
# 选项卡 2：iZotope Ozone 12 KS 预设卡片
# ==============================================================================
# ==============================================================================
# iZotope Ozone 12 独立完整设置窗口 (QDialog)
# ==============================================================================
class OzoneSettingsDialog(QDialog):
    def __init__(self, parent=None, preset_xml="", vst_dir=""):
        super().__init__(parent)
        self.setWindowTitle("⚙️ iZotope Ozone 12 母带预设与母带链详细设置")
        self.resize(860, 680)
        self.setMinimumSize(780, 520)

        self.preset_xml = preset_xml or OZONE_KS_XML_DEFAULT
        self.vst_dir = vst_dir or OZONE_VST3_DIR_DEFAULT
        self.saved = False

        self._init_ui()
        self._refresh_data()

    def _init_ui(self):
        self.setStyleSheet("""
            QDialog {
                background-color: #18181b;
                color: #f4f4f5;
            }
            QGroupBox {
                border: 1px solid #3f3f46;
                border-radius: 8px;
                margin-top: 12px;
                padding: 14px 10px 10px 10px;
                font-weight: bold;
                color: #38bdf8;
                background-color: #1e1e24;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 14px;
                padding: 0 6px;
            }
            QLineEdit {
                background-color: #27272a;
                border: 1px solid #3f3f46;
                border-radius: 6px;
                color: #f4f4f5;
                padding: 6px 10px;
                font-size: 12px;
            }
            QLineEdit:focus {
                border-color: #38bdf8;
            }
            QPushButton {
                background-color: #27272a;
                color: #e2e8f0;
                border: 1px solid #3f3f46;
                border-radius: 6px;
                padding: 6px 14px;
                font-size: 12px;
                font-weight: 500;
            }
            QPushButton:hover {
                background-color: #3f3f46;
                color: #ffffff;
                border-color: #38bdf8;
            }
            QPushButton#PrimaryBtn {
                background-color: #0284c7;
                color: #ffffff;
                font-weight: bold;
                border-color: #38bdf8;
            }
            QPushButton#PrimaryBtn:hover {
                background-color: #0369a1;
            }
            QScrollArea {
                border: 1px solid #27272a;
                border-radius: 6px;
                background-color: #18181b;
            }
        """)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(18, 16, 18, 16)
        main_layout.setSpacing(12)

        # 1. 顶部标头
        header_layout = QHBoxLayout()
        header_icon = QLabel("🎛️")
        header_icon.setStyleSheet("font-size: 32px;")
        header_layout.addWidget(header_icon)

        header_text = QVBoxLayout()
        header_text.setSpacing(2)
        lbl_head = QLabel("iZotope Ozone 12 工业级离线母带处理设置")
        lbl_head.setStyleSheet("font-size: 15px; font-weight: bold; color: #38bdf8;")
        lbl_desc = QLabel("纯正 64-bit 离线 DSP 高速渲染架构：免开重载图形界面，直接加载原生 VST3 算法执行商业级母带流水线。")
        lbl_desc.setStyleSheet("font-size: 11px; color: #94a3b8;")
        header_text.addWidget(lbl_head)
        header_text.addWidget(lbl_desc)
        header_layout.addLayout(header_text, stretch=1)
        main_layout.addLayout(header_layout)

        # 2. 路径配置组
        group_path = QGroupBox("📁 Ozone 预设与 VST3 核心目录")
        path_layout = QVBoxLayout(group_path)
        path_layout.setSpacing(8)

        # XML 预设行
        row_xml = QHBoxLayout()
        lbl_xml = QLabel("预设文件 (XML):")
        lbl_xml.setFixedWidth(110)
        lbl_xml.setStyleSheet("font-weight: 500; color: #cbd5e1;")
        self.txt_xml = QLineEdit(self.preset_xml)
        btn_browse_xml = QPushButton("📁 浏览...")
        btn_browse_xml.clicked.connect(self._on_browse_xml)
        btn_reload_xml = QPushButton("🔄 重新解析")
        btn_reload_xml.clicked.connect(self._refresh_data)
        btn_reset_ks = QPushButton("↺ 恢复 KS 预设")
        btn_reset_ks.clicked.connect(self._on_reset_ks)

        row_xml.addWidget(lbl_xml)
        row_xml.addWidget(self.txt_xml, stretch=1)
        row_xml.addWidget(btn_browse_xml)
        row_xml.addWidget(btn_reload_xml)
        row_xml.addWidget(btn_reset_ks)
        path_layout.addLayout(row_xml)

        # VST 目录行
        row_vst = QHBoxLayout()
        lbl_vst = QLabel("iZotope VST3 目录:")
        lbl_vst.setFixedWidth(110)
        lbl_vst.setStyleSheet("font-weight: 500; color: #cbd5e1;")
        self.txt_vst = QLineEdit(self.vst_dir)
        btn_browse_vst = QPushButton("📁 浏览...")
        btn_browse_vst.clicked.connect(self._on_browse_vst)

        row_vst.addWidget(lbl_vst)
        row_vst.addWidget(self.txt_vst, stretch=1)
        row_vst.addWidget(btn_browse_vst)
        path_layout.addLayout(row_vst)

        main_layout.addWidget(group_path)

        # 3. 模块卡片列表（滚动区域）
        group_modules = QGroupBox("⚡ 母带流水线激活模块清单 (按先后次序串联执行)")
        modules_box = QVBoxLayout(group_modules)
        modules_box.setContentsMargins(8, 10, 8, 8)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_widget = QWidget()
        self.scroll_layout = QVBoxLayout(self.scroll_widget)
        self.scroll_layout.setContentsMargins(6, 6, 6, 6)
        self.scroll_layout.setSpacing(8)
        self.scroll_area.setWidget(self.scroll_widget)

        modules_box.addWidget(self.scroll_area)
        main_layout.addWidget(group_modules, stretch=1)

        # 4. 底部按钮条
        bottom_bar = QHBoxLayout()
        bottom_bar.setSpacing(10)

        self.btn_self_test = QPushButton("🧪 运行母带链自检测试")
        self.btn_self_test.setToolTip("通过合成多频信号，测试 6 个 VST 模块是否均正常响应与渲染")
        self.btn_self_test.setStyleSheet("""
            QPushButton {
                background-color: #27272a;
                color: #a78bfa;
                border: 1px solid #7c3aed;
                padding: 7px 16px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #5b21b6;
                color: #ffffff;
            }
        """)
        self.btn_self_test.clicked.connect(self._on_self_test_clicked)

        self.lbl_tip = QLabel("提示: 修改预设后将自动用于选项卡 2 的母带增强处理")
        self.lbl_tip.setStyleSheet("font-size: 11px; color: #71717a;")

        self.btn_save = QPushButton("💾 保存并应用设置")
        self.btn_save.setObjectName("PrimaryBtn")
        self.btn_save.setFixedHeight(34)
        self.btn_save.clicked.connect(self._on_save_clicked)

        self.btn_cancel = QPushButton("取消 / 关闭")
        self.btn_cancel.setFixedHeight(34)
        self.btn_cancel.clicked.connect(self.reject)

        bottom_bar.addWidget(self.btn_self_test)
        bottom_bar.addWidget(self.lbl_tip)
        bottom_bar.addStretch(1)
        bottom_bar.addWidget(self.btn_save)
        bottom_bar.addWidget(self.btn_cancel)
        main_layout.addLayout(bottom_bar)

    def _on_browse_xml(self):
        curr = self.txt_xml.text().strip()
        init_dir = os.path.dirname(curr) if os.path.exists(curr) else os.path.expanduser("~")
        path, _ = QFileDialog.getOpenFileName(self, "选择 Ozone 预设文件 (XML)", init_dir, "Ozone 预设 (*.xml);;所有文件 (*.*)")
        if path:
            self.txt_xml.setText(path)
            self._refresh_data()

    def _on_reset_ks(self):
        self.txt_xml.setText(OZONE_KS_XML_DEFAULT)
        self._refresh_data()

    def _on_browse_vst(self):
        curr = self.txt_vst.text().strip()
        init_dir = curr if os.path.exists(curr) else r"C:\Program Files\Common Files\VST3\iZotope"
        dir_path = QFileDialog.getExistingDirectory(self, "选择 iZotope VST3 目录", init_dir)
        if dir_path:
            self.txt_vst.setText(dir_path)
            self._refresh_data()

    def _refresh_data(self):
        xml_path = self.txt_xml.text().strip()
        vst_dir = self.txt_vst.text().strip()

        # 清空旧模块卡片
        while self.scroll_layout.count():
            item = self.scroll_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        info = parse_ozone_preset_info(xml_path)
        if not info["valid"]:
            err_card = QFrame()
            err_card.setStyleSheet("background-color: #271c1c; border: 1px solid #7f1d1d; border-radius: 6px; padding: 12px;")
            l = QVBoxLayout(err_card)
            l.addWidget(QLabel(f"⚠️ 预设解析失败: {info['error']}"))
            l.addWidget(QLabel(f"路径: {xml_path}"))
            self.scroll_layout.addWidget(err_card)
            self.scroll_layout.addStretch(1)
            return

        for idx, mod in enumerate(info["modules"], 1):
            vst_filename = f"Ozone 12 {mod['name']}.vst3"
            vst_full_path = os.path.join(vst_dir, vst_filename)
            vst_exists = os.path.exists(vst_full_path)

            card = QFrame()
            card.setStyleSheet("""
                QFrame {
                    background-color: #1e1e24;
                    border: 1px solid #334155;
                    border-radius: 8px;
                    padding: 8px 12px;
                }
                QFrame:hover {
                    border-color: #38bdf8;
                    background-color: #24252f;
                }
            """)
            c_layout = QVBoxLayout(card)
            c_layout.setContentsMargins(6, 4, 6, 4)
            c_layout.setSpacing(4)

            # 头部行
            h_row = QHBoxLayout()
            lbl_title = QLabel(f"{mod['icon']} [{idx}/6] Ozone 12 {mod['name']} ({mod['tag']})")
            lbl_title.setStyleSheet("font-size: 13px; font-weight: bold; color: #38bdf8;")

            if vst_exists:
                lbl_status = QLabel("● 原生 VST3 模块就绪")
                lbl_status.setStyleSheet("color: #22c55e; font-size: 11px; font-weight: bold;")
            else:
                lbl_status = QLabel("⚠️ VST3 文件未找到")
                lbl_status.setStyleSheet("color: #ef4444; font-size: 11px; font-weight: bold;")

            h_row.addWidget(lbl_title)
            h_row.addStretch(1)
            h_row.addWidget(lbl_status)
            c_layout.addLayout(h_row)

            # 参数行
            lbl_param = QLabel(f"核心参数: {mod['detail']}")
            lbl_param.setStyleSheet("font-size: 12px; color: #e2e8f0; font-family: Consolas, monospace;")
            c_layout.addWidget(lbl_param)

            # 说明行
            purpose_map = {
                "Bass Control": "算法作用: 智能分离并重新塑造低频平衡、瞬态打击与延音能量，使低音深沉干净、底鼓凝聚不混浊。",
                "Dynamic EQ": "算法作用: 4 频段自适应动态均衡，高频自适应提亮通透度，低频增益饱满，中频自适应衰减浑浊谐波。",
                "Clarity": "算法作用: 心理声学高频自适应解蔽增强，瞬间消除音频中的蒙雾感，带来如同揭开幕布般的极高通透感与空气感。",
                "Equalizer": "算法作用: 针对母带频响曲线进行精准雕琢，补偿频率失衡，实现高保真度频率分布。",
                "Low End Focus": "算法作用: 针对低频瞬态轮廓进行对比度锐化与对焦增强，让低音在各种回放设备上都清晰可辨。",
                "Maximizer": "算法作用: 商业级 IRC IV 智能透明防削波母带最大化限制器，锁定 -0.3 dBFS 真实峰值，提供饱满冲击力。"
            }
            purpose = purpose_map.get(mod["name"], "算法作用: Ozone 原生专业母带音质处理")
            lbl_purpose = QLabel(purpose)
            lbl_purpose.setStyleSheet("font-size: 11px; color: #94a3b8;")
            c_layout.addWidget(lbl_purpose)

            self.scroll_layout.addWidget(card)

        self.scroll_layout.addStretch(1)

    def _on_self_test_clicked(self):
        xml_path = self.txt_xml.text().strip()
        vst_dir = self.txt_vst.text().strip()

        if not os.path.exists(xml_path):
            QMessageBox.critical(self, "自检失败", f"预设文件不存在:\n{xml_path}")
            return
        if not os.path.exists(vst_dir):
            QMessageBox.critical(self, "自检失败", f"VST3 目录不存在:\n{vst_dir}")
            return

        try:
            chain_modules, summaries = create_ozone_chain_from_xml(xml_path, vst_dir)
            if not chain_modules:
                QMessageBox.warning(self, "自检提示", "预设中未找到已启用的母带模块。")
                return

            # 合成 1 秒测试信号进行逐级验证
            sr = 44100
            t = np.linspace(0, 1.0, sr, endpoint=False)
            audio = (np.sin(2 * np.pi * 60 * t) * 0.3 * np.exp(-t % 0.5 * 8) +
                     np.sin(2 * np.pi * 200 * t) * 0.2 +
                     np.sin(2 * np.pi * 1000 * t) * 0.2 +
                     np.sin(2 * np.pi * 5000 * t) * 0.15 +
                     np.random.randn(len(t)) * 0.05).astype(np.float32)
            cur = np.vstack([audio, audio])

            report_lines = []
            for step, (name, proc, title, desc) in enumerate(chain_modules, 1):
                in_peak = float(np.max(np.abs(cur)))
                in_rms = float(np.sqrt(np.mean(cur**2)))
                out = proc(cur, sample_rate=sr)
                out_peak = float(np.max(np.abs(out)))
                out_rms = float(np.sqrt(np.mean(out**2)))
                min_len = min(out.shape[1], cur.shape[1])
                diff = float(np.max(np.abs(out[:, :min_len] - cur[:, :min_len])))

                in_peak_db = 20 * np.log10(max(in_peak, 1e-6))
                out_peak_db = 20 * np.log10(max(out_peak, 1e-6))
                delta_rms = 20 * np.log10(max(out_rms, 1e-6)) - 20 * np.log10(max(in_rms, 1e-6))

                report_lines.append(
                    f"[{step}/{len(chain_modules)}] {title}:\n"
                    f"    ✔ 状态: 正常运行 | 差异度={diff:.4f} | 增益={delta_rms:+.2f}dB | 峰值: {in_peak_db:.1f}dB ➔ {out_peak_db:.1f}dB"
                )
                cur = out

            msg = "✅ Ozone 12 预设母带链自检全部通过！\n\n各模块流水线执行测试报告:\n\n" + "\n".join(report_lines)
            QMessageBox.information(self, "母带链自检成功", msg)

        except Exception as e:
            QMessageBox.critical(self, "自检发生异常", f"执行自检时出错:\n{e}\n\n{traceback.format_exc()}")

    def _on_save_clicked(self):
        xml_path = self.txt_xml.text().strip()
        vst_dir = self.txt_vst.text().strip()

        if not os.path.exists(xml_path):
            QMessageBox.warning(self, "路径错误", f"预设文件不存在:\n{xml_path}")
            return
        if not os.path.exists(vst_dir):
            QMessageBox.warning(self, "路径错误", f"VST3 目录不存在:\n{vst_dir}")
            return

        self.preset_xml = xml_path
        self.vst_dir = vst_dir
        self.saved = True
        self.accept()


# ==============================================================================
# 选项卡 2：iZotope Ozone 12 KS 预设卡片 (主界面上清新整洁、不拥挤)
# ==============================================================================
class OzoneTabWidget(QFrame):
    preset_updated = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("OzoneCard")
        self.vst_dir = OZONE_VST3_DIR_DEFAULT
        self.preset_xml = OZONE_KS_XML_DEFAULT

        self.setStyleSheet("""
            QFrame#OzoneCard {
                background-color: #202129;
                border: 1px solid #323544;
                border-radius: 8px;
                padding: 12px 16px;
            }
            QFrame#OzoneCard:hover {
                border-color: #38bdf8;
            }
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(18, 12, 18, 12)
        layout.setSpacing(18)

        # 左侧大图标
        self.icon_label = QLabel("⚡")
        self.icon_label.setStyleSheet("font-size: 34px; color: #38bdf8;")
        layout.addWidget(self.icon_label)

        # 中间标题与状态说明
        info_col = QVBoxLayout()
        info_col.setSpacing(4)

        self.lbl_title = QLabel("iZotope Ozone 12 工业母带增强链 (KS 专属预设)")
        self.lbl_title.setStyleSheet("font-size: 14px; font-weight: bold; color: #38bdf8;")

        self.lbl_status = QLabel("● 预设就绪: KS.xml | 6 大原生母带模块已配置 (64-bit 离线高速渲染，无需参考歌)")
        self.lbl_status.setStyleSheet("font-size: 12px; color: #e2e8f0; font-weight: 500;")

        self.lbl_preset_path = QLabel("")
        self.lbl_preset_path.setStyleSheet("font-size: 11px; color: #94a3b8;")

        info_col.addWidget(self.lbl_title)
        info_col.addWidget(self.lbl_status)
        info_col.addWidget(self.lbl_preset_path)
        layout.addLayout(info_col, stretch=1)

        # 右侧操作按钮区
        btn_col = QVBoxLayout()
        btn_col.setSpacing(6)

        # 主按钮：打开完整设置对话框
        self.btn_open_settings = QPushButton("⚙️ 打开 Ozone 12 母带链设置与参数详情...")
        self.btn_open_settings.setFixedHeight(38)
        self.btn_open_settings.setStyleSheet("""
            QPushButton {
                background-color: #0284c7;
                color: #ffffff;
                font-size: 12px;
                font-weight: bold;
                padding: 0 16px;
                border: 1px solid #38bdf8;
                border-radius: 6px;
            }
            QPushButton:hover {
                background-color: #0369a1;
                border-color: #7dd3fc;
            }
            QPushButton:pressed {
                background-color: #075985;
            }
        """)
        self.btn_open_settings.clicked.connect(self._on_open_settings_clicked)

        # 次要按钮：快捷切换与重新解析
        sub_btn_row = QHBoxLayout()
        sub_btn_row.setSpacing(6)

        self.btn_choose_xml = QPushButton("📁 切换预设...")
        self.btn_choose_xml.setFixedHeight(28)
        self.btn_choose_xml.setStyleSheet("""
            QPushButton {
                background-color: #27272a;
                color: #cbd5e1;
                border: 1px solid #3f3f46;
                padding: 0 10px;
                border-radius: 5px;
                font-size: 11px;
            }
            QPushButton:hover { background-color: #3b4252; color: white; border-color: #38bdf8; }
        """)
        self.btn_choose_xml.clicked.connect(self._on_choose_xml_clicked)

        self.btn_reload = QPushButton("🔄 重新解析")
        self.btn_reload.setFixedHeight(28)
        self.btn_reload.setStyleSheet("""
            QPushButton {
                background-color: #27272a;
                color: #38bdf8;
                border: 1px solid #0284c7;
                padding: 0 10px;
                border-radius: 5px;
                font-size: 11px;
            }
            QPushButton:hover { background-color: #0369a1; color: white; }
        """)
        self.btn_reload.clicked.connect(self._refresh_status)

        sub_btn_row.addWidget(self.btn_choose_xml)
        sub_btn_row.addWidget(self.btn_reload)

        btn_col.addWidget(self.btn_open_settings)
        btn_col.addLayout(sub_btn_row)
        layout.addLayout(btn_col)

        self._refresh_status()

    def _refresh_status(self):
        info = parse_ozone_preset_info(self.preset_xml)
        if not info["valid"]:
            self.lbl_status.setText(f"⚠️ 预设文件错误: {info['error']}")
            self.lbl_status.setStyleSheet("font-size: 12px; color: #ef4444; font-weight: bold;")
            self.lbl_preset_path.setText(self.preset_xml)
            return

        preset_name = os.path.basename(self.preset_xml)
        self.lbl_title.setText(f"iZotope Ozone 12 工业母带增强链 ({preset_name})")
        self.lbl_status.setText(
            f"● 预设就绪: {preset_name} | {len(info['modules'])} 个原生工业母带模块已激活 (64-bit 离线高速渲染，无需参考歌)"
        )
        self.lbl_status.setStyleSheet("font-size: 12px; color: #38bdf8; font-weight: 500;")
        self.lbl_preset_path.setText(f"预设路径: {self.preset_xml}")

    def _on_choose_xml_clicked(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 Ozone 预设文件 (XML)", os.path.dirname(self.preset_xml), "Ozone 预设 (*.xml);;所有文件 (*.*)"
        )
        if path:
            self.preset_xml = path
            self._refresh_status()
            self.preset_updated.emit()

    def _on_open_settings_clicked(self):
        dlg = OzoneSettingsDialog(self, self.preset_xml, self.vst_dir)
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.saved:
            self.preset_xml = dlg.preset_xml
            self.vst_dir = dlg.vst_dir
            self._refresh_status()
            self.preset_updated.emit()


# ==============================================================================
# 选项卡 3：原曲直通卡片 (Direct Passthrough / 格式转换)
# ==============================================================================
class PassthroughTabWidget(QFrame):
    """选项卡 3：原曲直通 (仅转换格式 / 不做任何修改) 说明与卡片展示"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("PassthroughCard")
        self.setStyleSheet("""
            QFrame#PassthroughCard {
                background-color: #1a202c;
                border: 2px solid #334155;
                border-radius: 8px;
                padding: 10px;
            }
            QFrame#PassthroughCard:hover {
                border-color: #10b981;
                background-color: #1e293b;
            }
        """)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(18, 10, 18, 10)
        layout.setSpacing(16)

        lbl_icon = QLabel("🎵")
        lbl_icon.setStyleSheet("font-size: 38px;")
        layout.addWidget(lbl_icon)

        text_layout = QVBoxLayout()
        text_layout.setSpacing(4)

        title_row = QHBoxLayout()
        lbl_title = QLabel("原曲纯净直通 (Direct Audio Passthrough / 格式转换)")
        lbl_title.setStyleSheet("font-size: 14px; font-weight: bold; color: #34d399;")

        lbl_badge = QLabel("● 原曲波形零渲染损耗 | 仅转码格式与完整继承封面标签")
        lbl_badge.setStyleSheet("font-size: 11px; color: #10b981; font-weight: bold; padding-left: 8px;")

        title_row.addWidget(lbl_title)
        title_row.addWidget(lbl_badge)
        title_row.addStretch(1)
        text_layout.addLayout(title_row)

        lbl_desc1 = QLabel("⚡ <b>不做任何修改</b>：跳过 Matchering 与 Ozone 12 算法，不施加限制器压限与电平增益，100% 保持原始音频波形与动态。")
        lbl_desc1.setStyleSheet("font-size: 11px; color: #cbd5e1;")
        lbl_desc2 = QLabel("🏷️ <b>完整继承内嵌封面与元数据</b>：自动从源文件提取内置封面图片与全部 ID3/Vorbis 标签，一键无损迁移。")
        lbl_desc2.setStyleSheet("font-size: 11px; color: #94a3b8;")
        lbl_desc3 = QLabel("🎧 <b>高保真 MP3 / FLAC / WAV 导出</b>：支持直接转码导出为 320k / 192k MP3 以及各类规格 FLAC / WAV。")
        lbl_desc3.setStyleSheet("font-size: 11px; color: #60a5fa;")

        text_layout.addWidget(lbl_desc1)
        text_layout.addWidget(lbl_desc2)
        text_layout.addWidget(lbl_desc3)
        layout.addLayout(text_layout, stretch=1)


# ==============================================================================
# 主窗口界面
# ==============================================================================
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Matchering 2.0 - 音频参考母带与 Ozone 12 (KS) 音质增强批量工作台")
        self.resize(1180, 880)
        self.setMinimumSize(940, 700)

        # 加载持久化配置
        self.cfg = load_config()

        self.target_files = []
        self.worker = None

        self._init_ui()
        self._apply_theme()
        self._apply_loaded_config()

    def _init_ui(self):
        central_widget = QWidget(self)
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(16, 12, 16, 12)
        main_layout.setSpacing(10)

        # ----------------------------------------------------
        # 1. 顶部：三选项卡 (Matchering 参考母带 vs Ozone 12 预设增强 vs 原曲直通)
        # ----------------------------------------------------
        self.tab_widget = QTabWidget()
        self.tab_widget.setFixedHeight(150)

        # 选项卡 1：Matchering
        self.ref_card = DropReferenceWidget(self)
        self.tab_widget.addTab(self.ref_card, "🎚️ 选项卡 1: Matchering 目标参考母带 (需提供参考歌曲)")

        # 选项卡 2：Ozone 12 KS 预设增强
        self.ozone_card = OzoneTabWidget(self)
        self.tab_widget.addTab(self.ozone_card, "⚡ 选项卡 2: iZotope Ozone 12 (KS 预设) 音质提升增强 (免参考歌)")

        # 选项卡 3：原曲直通
        self.passthrough_card = PassthroughTabWidget(self)
        self.tab_widget.addTab(self.passthrough_card, "🎵 选项卡 3: 原曲直通 (仅转换格式 / 不做任何修改)")

        self.tab_widget.currentChanged.connect(self._on_tab_changed)
        main_layout.addWidget(self.tab_widget)

        # ----------------------------------------------------
        # 2. 中间：待制作音乐列表 (Target Tracks)
        # ----------------------------------------------------
        target_group = QGroupBox("待制作音乐列表 (Target Tracks - 支持单首/多首批量导入，拖拽文件或文件夹)")
        target_layout = QVBoxLayout(target_group)
        target_layout.setContentsMargins(12, 10, 12, 10)
        target_layout.setSpacing(8)

        btn_bar = QHBoxLayout()
        btn_bar.setSpacing(8)

        self.btn_add_files = QPushButton("➕ 添加文件...")
        self.btn_add_files.setToolTip("选择单首或批量多选音频")
        self.btn_add_files.clicked.connect(self._on_add_files)

        self.btn_add_folder = QPushButton("📁 添加文件夹...")
        self.btn_add_folder.setToolTip("选择文件夹自动扫描导入所有音乐")
        self.btn_add_folder.clicked.connect(self._on_add_folder)

        self.btn_remove_sel = QPushButton("➖ 移除选中")
        self.btn_remove_sel.clicked.connect(self._on_remove_selected)

        self.btn_clear_all = QPushButton("🗑️ 清空列表")
        self.btn_clear_all.clicked.connect(self._on_clear_all)

        self.lbl_count = QLabel("共 0 首歌曲")
        self.lbl_count.setStyleSheet("color: #94a3b8; font-weight: 500;")

        btn_bar.addWidget(self.btn_add_files)
        btn_bar.addWidget(self.btn_add_folder)
        btn_bar.addWidget(self.btn_remove_sel)
        btn_bar.addWidget(self.btn_clear_all)
        btn_bar.addStretch(1)
        btn_bar.addWidget(self.lbl_count)
        target_layout.addLayout(btn_bar)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels([
            "序号", "状态 / 进度", "待处理音乐", "规格", "大小", "耗时"
        ])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        self.table.setColumnWidth(1, 190)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setAcceptDrops(True)

        self.table.dragEnterEvent = self._table_dragEnterEvent
        self.table.dragMoveEvent = self._table_dragEnterEvent
        self.table.dropEvent = self._table_dropEvent

        target_layout.addWidget(self.table)
        main_layout.addWidget(target_group, stretch=2)

        # ----------------------------------------------------
        # 3. 输出响度 5 档精细调节
        # ----------------------------------------------------
        self.loudness_group = QGroupBox("输出响度 5 档调节 (解决母带响度过高、压限过重与炸耳问题)")
        loudness_layout = QVBoxLayout(self.loudness_group)
        loudness_layout.setContentsMargins(12, 10, 12, 10)
        loudness_layout.setSpacing(6)

        levels_btn_layout = QHBoxLayout()
        levels_btn_layout.setSpacing(6)

        self.loudness_btn_group = QButtonGroup(self)
        self.level_buttons = []

        for item in LOUDNESS_LEVELS:
            lvl = item["level"]
            btn = QPushButton(item["name"])
            btn.setCheckable(True)
            btn.setFixedHeight(34)
            btn.setStyleSheet("""
                QPushButton {
                    background-color: #27272a;
                    color: #d4d4d8;
                    border: 1px solid #3f3f46;
                    border-radius: 6px;
                    font-size: 12px;
                    font-weight: 500;
                    padding: 0 10px;
                }
                QPushButton:hover {
                    background-color: #3b4252;
                    border-color: #38bdf8;
                }
                QPushButton:checked {
                    background-color: #0284c7;
                    color: white;
                    border-color: #38bdf8;
                    font-weight: bold;
                }
            """)
            self.loudness_btn_group.addButton(btn, lvl)
            levels_btn_layout.addWidget(btn)
            self.level_buttons.append(btn)

        self.loudness_btn_group.idClicked.connect(self._on_loudness_level_changed)
        loudness_layout.addLayout(levels_btn_layout)

        self.lbl_loudness_desc = QLabel("")
        self.lbl_loudness_desc.setStyleSheet("font-size: 11px; color: #38bdf8; padding-left: 4px;")
        loudness_layout.addWidget(self.lbl_loudness_desc)

        main_layout.addWidget(self.loudness_group)

        # ----------------------------------------------------
        # 4. 输出格式与标签元数据配置 (支持保存)
        # ----------------------------------------------------
        meta_group = QGroupBox("输出格式 & 音乐元数据与封面配置 (适用于 MP3 / FLAC / WAV)")
        meta_vbox = QVBoxLayout(meta_group)
        meta_vbox.setContentsMargins(12, 10, 12, 10)
        meta_vbox.setSpacing(8)

        # 第一行：输出路径与格式
        row1 = QHBoxLayout()
        row1.setSpacing(10)

        lbl_dir = QLabel("输出保存目录:")
        lbl_dir.setStyleSheet("font-weight: 500; color: #e2e8f0;")
        self.txt_output_dir = QLineEdit()
        self.btn_browse_dir = QPushButton("📁 浏览...")
        self.btn_browse_dir.clicked.connect(self._on_browse_output_dir)
        self.btn_open_dir = QPushButton("📂 打开目录")
        self.btn_open_dir.clicked.connect(self._on_open_output_dir)

        lbl_fmt = QLabel("输出格式:")
        lbl_fmt.setStyleSheet("font-weight: 500; color: #e2e8f0;")
        self.combo_format = QComboBox()
        for code, (_, _, _, name) in AUDIO_FORMATS.items():
            self.combo_format.addItem(name, code)
        self._saved_mastering_format = DEFAULT_AUDIO_FORMAT

        self.lbl_suf = QLabel("后缀:")
        self.txt_suffix = QLineEdit("_mastered")
        self.txt_suffix.setFixedWidth(100)
        self.txt_suffix.setPlaceholderText("留空同名")

        row1.addWidget(lbl_dir)
        row1.addWidget(self.txt_output_dir, stretch=2)
        row1.addWidget(self.btn_browse_dir)
        row1.addWidget(self.btn_open_dir)
        row1.addWidget(lbl_fmt)
        row1.addWidget(self.combo_format)
        row1.addWidget(self.lbl_suf)
        row1.addWidget(self.txt_suffix)
        meta_vbox.addLayout(row1)

        # 第二行：音乐元数据标签
        row2 = QHBoxLayout()
        row2.setSpacing(10)

        lbl_art = QLabel("音乐家:")
        self.txt_artist = QLineEdit()
        self.txt_artist.setPlaceholderText("例如: 周杰伦 / 林俊杰")

        lbl_yr = QLabel("年代/年份:")
        self.txt_year = QLineEdit()
        self.txt_year.setPlaceholderText("例如: 2024")
        self.txt_year.setFixedWidth(80)

        lbl_alb = QLabel("专辑名称:")
        self.txt_album = QLineEdit()
        self.txt_album.setPlaceholderText("例如: 范特西 / 热门单曲集")

        lbl_gen = QLabel("音乐分类:")
        self.txt_genre = QLineEdit()
        self.txt_genre.setPlaceholderText("例如: 流行 / 电子 / 摇滚")
        self.txt_genre.setFixedWidth(120)

        row2.addWidget(lbl_art)
        row2.addWidget(self.txt_artist, stretch=1)
        row2.addWidget(lbl_yr)
        row2.addWidget(self.txt_year)
        row2.addWidget(lbl_alb)
        row2.addWidget(self.txt_album, stretch=1)
        row2.addWidget(lbl_gen)
        row2.addWidget(self.txt_genre)
        meta_vbox.addLayout(row2)

        # 第三行：同名封面与保存按钮
        row3 = QHBoxLayout()
        row3.setSpacing(12)

        self.chk_embed_cover = QCheckBox("自动寻找同目录音乐封面并压缩内嵌到音频 (优先匹配与音乐同名的 .jpg/png，例如 歌曲名.jpg)")
        self.chk_embed_cover.setChecked(True)
        self.chk_embed_cover.setToolTip("第一优先级：同目录下同名封面（如 Track01.wav 对应 Track01.jpg）；若无同名图，自动匹配 cover.jpg/folder.jpg 等。自动等比压缩为高质量轻量 JPEG 并嵌入 FLAC/WAV。")

        self.chk_limiter = QCheckBox("启用限制器 (Limiter)")
        self.chk_limiter.setChecked(True)

        self.btn_save_settings = QPushButton("💾 保存此设置为默认 (下次自动载入)")
        self.btn_save_settings.setStyleSheet("""
            QPushButton {
                background-color: #27272a;
                color: #38bdf8;
                border: 1px solid #0284c7;
                border-radius: 6px;
                padding: 5px 14px;
                font-weight: bold;
            }
            QPushButton:hover { background-color: #0369a1; color: white; }
        """)
        self.btn_save_settings.clicked.connect(self._on_save_settings_clicked)

        row3.addWidget(self.chk_embed_cover)
        row3.addWidget(self.chk_limiter)
        row3.addStretch(1)
        row3.addWidget(self.btn_save_settings)
        meta_vbox.addLayout(row3)

        main_layout.addWidget(meta_group)

        # ----------------------------------------------------
        # 5. 控制与进度展示区域
        # ----------------------------------------------------
        progress_box = QVBoxLayout()
        progress_box.setSpacing(5)

        prog_row = QHBoxLayout()
        self.lbl_progress_status = QLabel("就绪 (点击开始执行批量母带处理)")
        self.lbl_progress_status.setStyleSheet("font-size: 12px; color: #38bdf8; font-weight: bold;")
        self.lbl_progress_pct = QLabel("0 / 0 (0%)")
        self.lbl_progress_pct.setStyleSheet("font-size: 12px; color: #94a3b8;")
        prog_row.addWidget(self.lbl_progress_status)
        prog_row.addStretch(1)
        prog_row.addWidget(self.lbl_progress_pct)
        progress_box.addLayout(prog_row)

        self.prog_overall = QProgressBar()
        self.prog_overall.setValue(0)
        self.prog_overall.setFixedHeight(18)
        progress_box.addWidget(self.prog_overall)
        main_layout.addLayout(progress_box)

        bottom_bar = QHBoxLayout()
        bottom_bar.setSpacing(12)

        self.btn_start = QPushButton("🚀 开始批量母带制作")
        self.btn_start.setFixedHeight(42)
        self.btn_start.setStyleSheet("""
            QPushButton {
                background-color: #10b981;
                color: white;
                font-size: 14px;
                font-weight: bold;
                border-radius: 7px;
                padding: 0 28px;
            }
            QPushButton:hover { background-color: #059669; }
            QPushButton:disabled { background-color: #374151; color: #6b7280; }
        """)
        self.btn_start.clicked.connect(self._on_start_clicked)

        self.btn_stop = QPushButton("⏹️ 终止处理")
        self.btn_stop.setFixedHeight(42)
        self.btn_stop.setEnabled(False)
        self.btn_stop.setStyleSheet("""
            QPushButton {
                background-color: #ef4444;
                color: white;
                font-size: 14px;
                font-weight: bold;
                border-radius: 7px;
                padding: 0 20px;
            }
            QPushButton:hover { background-color: #dc2626; }
            QPushButton:disabled { background-color: #374151; color: #6b7280; }
        """)
        self.btn_stop.clicked.connect(self._on_stop_clicked)

        self.btn_toggle_log = QPushButton("📜 查看处理日志 ▼")
        self.btn_toggle_log.setFixedHeight(34)
        self.btn_toggle_log.clicked.connect(self._on_toggle_log)

        bottom_bar.addWidget(self.btn_start, stretch=2)
        bottom_bar.addWidget(self.btn_stop, stretch=1)
        bottom_bar.addStretch(1)
        bottom_bar.addWidget(self.btn_toggle_log)
        main_layout.addLayout(bottom_bar)

        self.log_console = QTextEdit()
        self.log_console.setReadOnly(True)
        self.log_console.setFixedHeight(120)
        self.log_console.setVisible(False)
        self.log_console.setStyleSheet("""
            QTextEdit {
                background-color: #111217;
                color: #cbd5e1;
                font-family: 'Consolas', 'Courier New', monospace;
                font-size: 11px;
                border: 1px solid #334155;
                border-radius: 6px;
                padding: 6px;
            }
        """)
        main_layout.addWidget(self.log_console)

    def _apply_theme(self):
        dark_qss = """
        QMainWindow {
            background-color: #18191f;
        }
        QWidget {
            color: #f1f5f9;
            font-family: 'Segoe UI', 'Microsoft YaHei', sans-serif;
            font-size: 12px;
        }
        QTabWidget::pane {
            border: 1px solid #323544;
            border-radius: 8px;
            background-color: #202129;
            top: -1px;
        }
        QTabBar::tab {
            background-color: #1a1b22;
            color: #94a3b8;
            border: 1px solid #2d3142;
            border-bottom: none;
            border-top-left-radius: 8px;
            border-top-right-radius: 8px;
            padding: 8px 18px;
            margin-right: 4px;
            font-size: 12px;
            font-weight: 500;
        }
        QTabBar::tab:selected {
            background-color: #202129;
            color: #38bdf8;
            border-color: #3b82f6;
            border-bottom: 2px solid #38bdf8;
            font-weight: bold;
        }
        QTabBar::tab:hover:!selected {
            background-color: #252834;
            color: #e2e8f0;
        }
        QGroupBox {
            background-color: #202129;
            border: 1px solid #323544;
            border-radius: 7px;
            margin-top: 10px;
            padding-top: 12px;
            font-weight: bold;
            color: #cbd5e1;
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            subcontrol-position: top left;
            left: 14px;
            padding: 0 4px;
        }
        QPushButton {
            background-color: #2e3140;
            color: #f1f5f9;
            border: 1px solid #43475c;
            border-radius: 6px;
            padding: 5px 12px;
        }
        QPushButton:hover {
            background-color: #3b3f54;
            border-color: #555a75;
        }
        QLineEdit {
            background-color: #14151b;
            border: 1px solid #383c4f;
            border-radius: 6px;
            padding: 5px 9px;
            color: #f8fafc;
        }
        QLineEdit:focus {
            border-color: #3b82f6;
        }
        QComboBox {
            background-color: #14151b;
            border: 1px solid #383c4f;
            border-radius: 6px;
            padding: 4px 10px;
            color: #f8fafc;
        }
        QComboBox QAbstractItemView {
            background-color: #202129;
            selection-background-color: #3b82f6;
            color: #f8fafc;
        }
        QCheckBox {
            color: #e2e8f0;
            spacing: 6px;
        }
        QCheckBox::indicator {
            width: 15px;
            height: 15px;
            border-radius: 3px;
            border: 1px solid #475569;
            background-color: #14151b;
        }
        QCheckBox::indicator:checked {
            background-color: #3b82f6;
            border-color: #3b82f6;
        }
        QTableWidget {
            background-color: #14151b;
            alternate-background-color: #191b24;
            border: 1px solid #2d3142;
            border-radius: 6px;
            gridline-color: #262938;
            color: #f1f5f9;
        }
        QTableWidget::item:selected {
            background-color: #2c3c63;
        }
        QHeaderView::section {
            background-color: #20222d;
            color: #94a3b8;
            padding: 5px;
            border: none;
            border-right: 1px solid #282c3c;
            border-bottom: 1px solid #282c3c;
            font-weight: bold;
        }
        QProgressBar {
            background-color: #14151b;
            border: 1px solid #2d3142;
            border-radius: 5px;
            text-align: center;
            color: #f8fafc;
            font-size: 11px;
            font-weight: bold;
        }
        QProgressBar::chunk {
            background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #2563eb, stop:1 #38bdf8);
            border-radius: 4px;
        }
        """
        self.setStyleSheet(dark_qss)

    def _apply_loaded_config(self):
        cfg = self.cfg
        self.txt_output_dir.setText(cfg.get("output_dir", ""))
        self.txt_suffix.setText(cfg.get("filename_suffix", "_mastered"))
        self._saved_mastering_suffix = self.txt_suffix.text().strip() or "_mastered"
        self.chk_limiter.setChecked(cfg.get("use_limiter", True))
        self.chk_embed_cover.setChecked(cfg.get("embed_cover", True))

        # 母带输出格式记忆（若保存的是MP3，母带默认使用 24-bit 96kHz FLAC）
        mastering_fmt = cfg.get("mastering_output_format", cfg.get("output_format", DEFAULT_AUDIO_FORMAT))
        if mastering_fmt == "FLAC_24" or mastering_fmt in ("MP3_320", "MP3_192"):
            mastering_fmt = DEFAULT_AUDIO_FORMAT
        self._saved_mastering_format = mastering_fmt

        # 选项卡恢复
        active_tab = cfg.get("active_tab", 0)
        self.tab_widget.setCurrentIndex(active_tab)

        # 输出格式恢复
        fmt = cfg.get("output_format", DEFAULT_AUDIO_FORMAT)
        if fmt == "FLAC_24":
            fmt = DEFAULT_AUDIO_FORMAT
        if active_tab == 2:
            idx = self.combo_format.findData("MP3_192")
        else:
            idx = self.combo_format.findData(fmt)
        if idx >= 0:
            self.combo_format.setCurrentIndex(idx)
        else:
            self.combo_format.setCurrentIndex(0)

        # 5 档响度
        target_lvl = cfg.get("loudness_level", 3)
        btn = self.loudness_btn_group.button(target_lvl)
        if btn:
            btn.setChecked(True)
            self._on_loudness_level_changed(target_lvl)
        else:
            self.level_buttons[2].setChecked(True)
            self._on_loudness_level_changed(3)

        # 元数据
        meta = cfg.get("metadata", {})
        self.txt_artist.setText(meta.get("artist", ""))
        self.txt_year.setText(meta.get("year", ""))
        self.txt_album.setText(meta.get("album", ""))
        self.txt_genre.setText(meta.get("genre", ""))

        # Ozone 预设与 VST 目录恢复
        if "ozone_preset_path" in cfg and os.path.exists(cfg["ozone_preset_path"]):
            self.ozone_card.preset_xml = cfg["ozone_preset_path"]
        if "ozone_vst_dir" in cfg and os.path.exists(cfg["ozone_vst_dir"]):
            self.ozone_card.vst_dir = cfg["ozone_vst_dir"]
        self.ozone_card._refresh_status()

        # 触发当前选项卡 UI 联动
        self._on_tab_changed(self.tab_widget.currentIndex())

    def _set_loudness_group_enabled(self, enabled: bool):
        self.loudness_group.setEnabled(enabled)
        for btn in self.level_buttons:
            btn.setEnabled(enabled)

    def _on_tab_changed(self, idx: int):
        if idx in (0, 1):
            # 选项卡 1: Matchering / 选项卡 2: Ozone 12
            self._set_loudness_group_enabled(True)
            self.chk_limiter.setEnabled(True)
            if hasattr(self, "lbl_suf"):
                self.lbl_suf.setText("后缀:")
            mastering_suf = getattr(self, "_saved_mastering_suffix", "_mastered") or "_mastered"
            if not self.txt_suffix.text().strip():
                self.txt_suffix.setText(mastering_suf)
            self.txt_suffix.setPlaceholderText("_mastered")

            # 若先前在原曲直通模式下自动选为 MP3，切回母带模式时自动切回母带格式（默认 24-bit / 96 kHz FLAC）
            current_fmt = self.combo_format.currentData()
            if current_fmt in ("MP3_320", "MP3_192"):
                mastering_fmt = getattr(self, "_saved_mastering_format", DEFAULT_AUDIO_FORMAT) or DEFAULT_AUDIO_FORMAT
                idx_fmt = self.combo_format.findData(mastering_fmt)
                if idx_fmt >= 0:
                    self.combo_format.setCurrentIndex(idx_fmt)

            if idx == 0:
                self.btn_start.setText("🚀 开始 Matchering 批量参考母带")
                self.btn_start.setStyleSheet("""
                    QPushButton {
                        background-color: #10b981;
                        color: white;
                        font-size: 14px;
                        font-weight: bold;
                        border-radius: 7px;
                        padding: 0 28px;
                    }
                    QPushButton:hover { background-color: #059669; }
                    QPushButton:disabled { background-color: #374151; color: #6b7280; }
                """)
                self.lbl_progress_status.setText("就绪 (当前: Matchering 参考母带模式)")
            else:
                self.btn_start.setText("⚡ 开始 Ozone 12 (KS) 预设批量增强")
                self.btn_start.setStyleSheet("""
                    QPushButton {
                        background-color: #0284c7;
                        color: white;
                        font-size: 14px;
                        font-weight: bold;
                        border-radius: 7px;
                        padding: 0 28px;
                    }
                    QPushButton:hover { background-color: #0369a1; }
                    QPushButton:disabled { background-color: #374151; color: #6b7280; }
                """)
                self.lbl_progress_status.setText("就绪 (当前: Ozone 12 预设增强模式)")
        else:
            # 选项卡 3: 原曲直通 (仅转换格式 / 不做任何修改)
            self._set_loudness_group_enabled(False)
            self.chk_limiter.setEnabled(False)
            current_suf = self.txt_suffix.text().strip()
            if current_suf:
                self._saved_mastering_suffix = current_suf
            # 原曲直通模式默认无后缀，保持与原曲同名（仅扩展名改变）
            self.txt_suffix.setText("")
            self.txt_suffix.setPlaceholderText("留空同名")
            if hasattr(self, "lbl_suf"):
                self.lbl_suf.setText("后缀 (可选):")

            # 暂存先前的母带输出格式（若当前非 MP3）
            current_fmt = self.combo_format.currentData()
            if current_fmt and current_fmt not in ("MP3_320", "MP3_192"):
                self._saved_mastering_format = current_fmt

            # 原曲直通模式下：输出格式自动选择 192k mp3
            idx_mp3 = self.combo_format.findData("MP3_192")
            if idx_mp3 >= 0:
                self.combo_format.setCurrentIndex(idx_mp3)

            self.btn_start.setText("🎵 开始原曲批量转换导出 (无损直通)")
            self.btn_start.setStyleSheet("""
                QPushButton {
                    background-color: #059669;
                    color: white;
                    font-size: 14px;
                    font-weight: bold;
                    border-radius: 7px;
                    padding: 0 28px;
                }
                QPushButton:hover { background-color: #047857; }
                QPushButton:disabled { background-color: #374151; color: #6b7280; }
            """)
            self.lbl_progress_status.setText("就绪 (当前: 原曲直通模式 - 音频不做任何修改，仅转换格式与继承封面元数据)")

    def _on_loudness_level_changed(self, level_id: int):
        for item in LOUDNESS_LEVELS:
            if item["level"] == level_id:
                self.lbl_loudness_desc.setText(
                    f"当前选择: <b>{item['name']}</b> —— {item['desc']}"
                )
                break

    def _get_current_loudness_db(self) -> float:
        checked_id = self.loudness_btn_group.checkedId()
        for item in LOUDNESS_LEVELS:
            if item["level"] == checked_id:
                return item["db"]
        return -3.0

    def _on_save_settings_clicked(self):
        self.cfg["active_tab"] = self.tab_widget.currentIndex()
        self.cfg["output_dir"] = self.txt_output_dir.text().strip()
        self.cfg["output_format"] = self.combo_format.currentData()
        if hasattr(self, "_saved_mastering_format"):
            self.cfg["mastering_output_format"] = self._saved_mastering_format
        self.cfg["loudness_level"] = self.loudness_btn_group.checkedId()
        if self.tab_widget.currentIndex() == 2 and not self.txt_suffix.text().strip():
            self.cfg["filename_suffix"] = getattr(self, "_saved_mastering_suffix", "_mastered")
        else:
            self.cfg["filename_suffix"] = self.txt_suffix.text().strip()
        self.cfg["use_limiter"] = self.chk_limiter.isChecked()
        self.cfg["embed_cover"] = self.chk_embed_cover.isChecked()
        self.cfg["ozone_preset_path"] = self.ozone_card.preset_xml
        self.cfg["ozone_vst_dir"] = self.ozone_card.vst_dir
        self.cfg["metadata"] = {
            "artist": self.txt_artist.text().strip(),
            "year": self.txt_year.text().strip(),
            "album": self.txt_album.text().strip(),
            "genre": self.txt_genre.text().strip()
        }
        save_config(self.cfg)
        QMessageBox.information(
            self, "保存成功",
            "当前设置（当前选项卡、输出格式、5档响度、音乐家、年代、专辑名称、分类等）已成功保存！\n下次启动软件时将自动载入，无需重新输入。"
        )

    # --------------------------------------------------------------------------
    # 文件列表管理与拖拽
    # --------------------------------------------------------------------------
    def _on_add_files(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "选择待制作音乐 (支持单首或批量多选)", "", AUDIO_FILTER_STR
        )
        if files:
            self._add_target_paths(files)

    def _on_add_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "选择包含音频的文件夹")
        if folder:
            audio_files = []
            for root, _, files in os.walk(folder):
                for f in sorted(files):
                    if Path(f).suffix.lower() in SUPPORTED_EXTENSIONS:
                        audio_files.append(os.path.join(root, f))
            if audio_files:
                self._add_target_paths(audio_files)
            else:
                QMessageBox.information(self, "提示", "所选文件夹内未发现支持的音频文件。")

    def _table_dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def _table_dropEvent(self, event: QDropEvent):
        paths_to_add = []
        for url in event.mimeData().urls():
            file_path = url.toLocalFile()
            if os.path.isdir(file_path):
                for root, _, files in os.walk(file_path):
                    for f in sorted(files):
                        if Path(f).suffix.lower() in SUPPORTED_EXTENSIONS:
                            paths_to_add.append(os.path.join(root, f))
            elif os.path.isfile(file_path):
                if Path(file_path).suffix.lower() in SUPPORTED_EXTENSIONS:
                    paths_to_add.append(file_path)

        if paths_to_add:
            self._add_target_paths(paths_to_add)
            event.acceptProposedAction()

    def _add_target_paths(self, paths: list):
        existing_paths = {p for p, _, _ in self.target_files}
        added_count = 0

        for path in paths:
            norm_path = os.path.normpath(path)
            if norm_path not in existing_paths and os.path.isfile(norm_path):
                size_str = format_size(os.path.getsize(norm_path))
                info_str = get_audio_info_str(norm_path)
                self.target_files.append((norm_path, size_str, info_str))
                existing_paths.add(norm_path)

                row = self.table.rowCount()
                self.table.insertRow(row)

                item_idx = QTableWidgetItem(str(row + 1))
                item_idx.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

                item_status = QTableWidgetItem("等待处理")
                item_status.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                item_status.setForeground(QColor("#94a3b8"))

                item_name = QTableWidgetItem(os.path.basename(norm_path))
                item_name.setToolTip(norm_path)

                item_info = QTableWidgetItem(info_str)
                item_info.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

                item_size = QTableWidgetItem(size_str)
                item_size.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

                item_time = QTableWidgetItem("-")
                item_time.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

                self.table.setItem(row, 0, item_idx)
                self.table.setItem(row, 1, item_status)
                self.table.setItem(row, 2, item_name)
                self.table.setItem(row, 3, item_info)
                self.table.setItem(row, 4, item_size)
                self.table.setItem(row, 5, item_time)

                added_count += 1

        self._update_counter()
        if added_count > 0:
            self._append_log("INFO", f"已添加 {added_count} 首待制作歌曲到队列。")

    def _on_remove_selected(self):
        selected_rows = sorted(set(index.row() for index in self.table.selectedIndexes()), reverse=True)
        if not selected_rows:
            return
        for r in selected_rows:
            self.table.removeRow(r)
            del self.target_files[r]

        for i in range(self.table.rowCount()):
            item = self.table.item(i, 0)
            if item:
                item.setText(str(i + 1))

        self._update_counter()

    def _on_clear_all(self):
        if not self.target_files:
            return
        self.table.setRowCount(0)
        self.target_files.clear()
        self._update_counter()
        self.prog_overall.setValue(0)
        self.lbl_progress_status.setText("就绪")
        self.lbl_progress_pct.setText("0 / 0 (0%)")

    def _update_counter(self):
        count = len(self.target_files)
        self.lbl_count.setText(f"共 {count} 首歌曲")
        self.btn_start.setEnabled(count > 0 and self.worker is None)

    # --------------------------------------------------------------------------
    # 输出目录与日志
    # --------------------------------------------------------------------------
    def _on_browse_output_dir(self):
        current_dir = self.txt_output_dir.text().strip() or os.getcwd()
        folder = QFileDialog.getExistingDirectory(self, "选择输出保存目录", current_dir)
        if folder:
            self.txt_output_dir.setText(os.path.abspath(folder))

    def _on_open_output_dir(self):
        out_dir = self.txt_output_dir.text().strip()
        if not os.path.exists(out_dir):
            os.makedirs(out_dir, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(out_dir))

    def _on_toggle_log(self):
        is_vis = self.log_console.isVisible()
        self.log_console.setVisible(not is_vis)
        self.btn_toggle_log.setText("📜 收起处理日志 ▲" if not is_vis else "📜 查看处理日志 ▼")

    def _append_log(self, level: str, msg: str):
        color_map = {
            "INFO": "#38bdf8",
            "SUCCESS": "#34d399",
            "WARN": "#fbbf24",
            "ERROR": "#f87171",
            "SYSTEM": "#c084fc",
            "TASK": "#f472b6",
            "DEBUG": "#64748b"
        }
        color = color_map.get(level, "#cbd5e1")
        time_str = time.strftime("%H:%M:%S")
        html = f"<span style='color:#64748b;'>[{time_str}]</span> <span style='color:{color};'>{msg}</span>"
        self.log_console.append(html)

    # --------------------------------------------------------------------------
    # 批量母带制作执行控制
    # --------------------------------------------------------------------------
    def _on_start_clicked(self):
        active_tab_idx = self.tab_widget.currentIndex()
        if active_tab_idx == 0:
            engine_mode = "matchering"
        elif active_tab_idx == 1:
            engine_mode = "ozone12"
        else:
            engine_mode = "passthrough"

        ref_file = ""
        if engine_mode == "matchering":
            ref_file = self.ref_card.current_file
            if not ref_file or not os.path.isfile(ref_file):
                QMessageBox.warning(
                    self, "缺少参考歌曲",
                    "当前处于【Matchering 选项卡】，请先选择一首【参考歌曲 (Reference Track)】！\n\n如无需参考歌曲，请切换到【Ozone 12 (KS 预设) 选项卡】进行音质增强，或切换至【原曲直通 选项卡】直接转换格式。"
                )
                return
        elif engine_mode == "ozone12":
            if not os.path.exists(self.ozone_card.preset_xml):
                QMessageBox.critical(
                    self, "缺少 Ozone 预设",
                    f"未找到 Ozone 预设文件:\n{self.ozone_card.preset_xml}\n请点击【切换预设 (XML)...】重新指定。"
                )
                return
            if not os.path.exists(self.ozone_card.vst_dir):
                QMessageBox.critical(
                    self, "缺少 Ozone 12 模块",
                    f"未找到 iZotope VST3 模块目录:\n{self.ozone_card.vst_dir}\n请确认已安装 iZotope Ozone 12。"
                )
                return

        if not self.target_files:
            QMessageBox.warning(self, "待制作列表为空", "请先添加至少一首待制作的音乐！")
            return

        output_dir = self.txt_output_dir.text().strip()
        if not output_dir:
            QMessageBox.warning(self, "输出目录未设置", "请指定结果音频的保存目录！")
            return

        tasks = []
        for row_idx, (path, _, _) in enumerate(self.target_files):
            tasks.append((row_idx, path))
            item = self.table.item(row_idx, 1)
            if item:
                item.setText("等待处理")
                item.setForeground(QColor("#94a3b8"))

        format_code = self.combo_format.currentData()
        loudness_db = self._get_current_loudness_db()
        suffix = self.txt_suffix.text().strip()
        if engine_mode == "passthrough" and suffix in ("_mastered", "-Mastered"):
            suffix = ""
        use_limiter = self.chk_limiter.isChecked()
        embed_cover = self.chk_embed_cover.isChecked()

        metadata = {
            "artist": self.txt_artist.text().strip(),
            "year": self.txt_year.text().strip(),
            "album": self.txt_album.text().strip(),
            "genre": self.txt_genre.text().strip()
        }

        ozone_modules = None
        ozone_summaries = None
        if engine_mode == "ozone12":
            self.lbl_progress_status.setText("正在主线程预装配 Ozone 12 原生母带流水线...")
            self._append_log("INFO", f"正在主线程预装配 Ozone 12 预设母带链: {self.ozone_card.preset_xml} ...")
            try:
                ozone_modules, ozone_summaries = create_ozone_chain_from_xml(
                    self.ozone_card.preset_xml, self.ozone_card.vst_dir
                )
                self._append_log("SUCCESS", f"✅ 主线程装配完成: 共 {len(ozone_modules)} 个原生母带模块已就绪")
            except Exception as e:
                QMessageBox.critical(self, "Ozone 加载失败", f"无法在主线程装配 Ozone 12 预设母带链:\n{e}")
                self._append_log("ERROR", f"❌ 主线程装配 Ozone 12 预设母带链失败: {e}")
                return

        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.btn_add_files.setEnabled(False)
        self.btn_add_folder.setEnabled(False)
        self.btn_remove_sel.setEnabled(False)
        self.btn_clear_all.setEnabled(False)
        self.tab_widget.setEnabled(False)

        self.prog_overall.setMaximum(len(tasks))
        self.prog_overall.setValue(0)
        if engine_mode == "matchering":
            mode_text = "Matchering 参考母带"
        elif engine_mode == "ozone12":
            mode_text = "Ozone 12 (KS) 预设增强"
        else:
            mode_text = "原曲纯净格式转换 (直通)"
        self.lbl_progress_status.setText(f"准备开始【{mode_text}】处理共 {len(tasks)} 首歌曲...")
        self.lbl_progress_pct.setText(f"0 / {len(tasks)} (0%)")

        self.worker = MasteringWorker(
            engine_mode=engine_mode,
            target_tasks=tasks,
            output_dir=output_dir,
            reference_path=ref_file,
            ozone_preset_path=self.ozone_card.preset_xml,
            ozone_vst_dir=self.ozone_card.vst_dir,
            format_code=format_code,
            loudness_db=loudness_db,
            filename_suffix=suffix,
            use_limiter=use_limiter,
            normalize=True,
            metadata=metadata,
            embed_cover=embed_cover,
            ozone_chain_modules=ozone_modules,
            ozone_summary_list=ozone_summaries,
            parent=self
        )
        self.worker.task_started.connect(self._on_worker_task_started)
        self.worker.task_progress.connect(self._on_worker_task_progress)
        self.worker.task_finished.connect(self._on_worker_task_finished)
        self.worker.batch_progress.connect(self._on_worker_batch_progress)
        self.worker.batch_finished.connect(self._on_worker_batch_finished)
        self.worker.log_emitted.connect(self._append_log)
        self.worker.start()

    def _on_stop_clicked(self):
        if self.worker and self.worker.isRunning():
            self.lbl_progress_status.setText("正在终止处理，请稍候...")
            self.btn_stop.setEnabled(False)
            self.worker.cancel()

    # --------------------------------------------------------------------------
    # 工作线程信号槽响应
    # --------------------------------------------------------------------------
    def _on_worker_task_started(self, row_idx: int, filename: str):
        item = self.table.item(row_idx, 1)
        if item:
            item.setText("正在载入...")
            item.setForeground(QColor("#38bdf8"))
        self.lbl_progress_status.setText(f"正在母带处理: {filename}")

    def _on_worker_task_progress(self, row_idx: int, pct: int, stage_text: str):
        item = self.table.item(row_idx, 1)
        if item:
            item.setText(f"{stage_text} ({pct}%)")
            item.setForeground(QColor("#60a5fa"))
        track_name = os.path.basename(self.target_files[row_idx][0])
        self.lbl_progress_status.setText(f"{track_name} - {stage_text}")

    def _on_worker_task_finished(
        self, row_idx: int, success: bool, output_path: str, elapsed_sec: float, msg: str
    ):
        item_status = self.table.item(row_idx, 1)
        if item_status:
            if success:
                item_status.setText("✅ 已完成")
                item_status.setForeground(QColor("#34d399"))
            elif msg == "已取消":
                item_status.setText("⏹️ 已取消")
                item_status.setForeground(QColor("#fbbf24"))
            else:
                item_status.setText("❌ 失败")
                item_status.setForeground(QColor("#f87171"))
                item_status.setToolTip(msg)

        item_time = self.table.item(row_idx, 5)
        if item_time:
            item_time.setText(f"{elapsed_sec:.1f}s")

    def _on_worker_batch_progress(self, current_completed: int, total_count: int):
        self.prog_overall.setValue(current_completed)
        pct = int(current_completed / total_count * 100) if total_count > 0 else 0
        self.lbl_progress_pct.setText(f"{current_completed} / {total_count} ({pct}%)")

    def _on_worker_batch_finished(
        self, success_count: int, failed_count: int, total_elapsed: float
    ):
        self.worker = None
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.btn_add_files.setEnabled(True)
        self.btn_add_folder.setEnabled(True)
        self.btn_remove_sel.setEnabled(True)
        self.btn_clear_all.setEnabled(True)
        self.tab_widget.setEnabled(True)

        summary_text = (
            f"批量处理完成！成功 {success_count} 首，失败 {failed_count} 首，总耗时 {total_elapsed:.1f} 秒。"
        )
        self.lbl_progress_status.setText(summary_text)
        self._append_log("SYSTEM", f"\n🏁 {summary_text}")

        if success_count > 0:
            res = QMessageBox.information(
                self, "处理完成",
                f"{summary_text}\n\n所有母带音频已保存至输出目录。\n是否立即在资源管理器中打开输出目录？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes
            )
            if res == QMessageBox.StandardButton.Yes:
                self._on_open_output_dir()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Matchering GUI")

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
