import os
import sys
import tempfile
import bisect

from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
                             QPushButton, QTextEdit, QPlainTextEdit, QFileDialog, QLabel,
                             QProgressBar, QMessageBox, QGroupBox, QComboBox, QCheckBox,
                             QLineEdit, QStyleFactory, QStackedWidget, QFrame, QScrollArea,
                             QMenu)
from PySide6.QtCore import Qt, QThread, Signal, QSettings
from PySide6.QtGui import (QIcon, QColor, QPalette, QTextCursor,
                           QKeySequence, QShortcut, QAction, QFont,
                           QPainter, QPainterPath, QPen, QFontMetricsF,
                           QFontDatabase, QRawFont, QTextCharFormat)

from opencc import OpenCC

from constants import VERSION
from updater import UpdateChecker
from ambiguity_text import convert_with_ambiguities, query_candidates, AmbiguityConversionError
from text_converter import convert_txt_file, convert_srt_file, convert_ass_file, convert_lrc_file
from doc_converter import convert_docx_file
from epub_converter import convert_epub_file
from pdf_converter import convert_pdf_file
from custom_dict import parse_custom_entries, build_custom_config_file

# 转换类型显示名 -> OpenCC 配置名
CONVERSION_TYPES = {
    "简体到繁体（OpenCC标准）": "s2t",
    "繁体（OpenCC标准）到简体": "t2s",
    "简体到繁体（《通用规范汉字表》标准）": "s2tg",
    "繁体（《通用规范汉字表》标准）到简体": "tg2s",
    "繁体（任意标准）到繁体（《通用规范汉字表》标准）": "t2gov",
    "简体到台湾正体": "s2tw",
    "台湾正体到简体": "tw2s",
    "简体到香港繁体": "s2hk",
    "香港繁体到简体": "hk2s",
    "简体到繁体（台湾正体标准）并转换为台湾常用词汇": "s2twp",
    "繁体（台湾正体标准）到简体并转换为大陆常用词汇": "tw2sp",
    "繁体（OpenCC标准）到台湾正体": "t2tw",
    "台湾正体到繁体（OpenCC标准）": "tw2t",
    "香港繁体到繁体（OpenCC标准）": "hk2t",
    "繁体（OpenCC标准）到香港繁体": "t2hk",
    "简体到香港繁体（香港常用词汇）": "s2hkp",
    "香港繁体到简体（大陆常用词汇）": "hk2sp",
    "繁体（OpenCC标准，旧字体）到日文新字体": "t2jp",
    "日文新字体到繁体（OpenCC标准，旧字体）": "jp2t",
    "繁体汉字到小篆（Unicode 18.0 篆书区块）": "t2seal",
    "简体到小篆（Unicode 18.0 篆书区块）": "s2seal",
    "小篆（Unicode 18.0 篆书区块）到繁体汉字": "seal2t"
}

# 小篆显示字体：AGPL 授权，不随本软件分发，需用户自行安装到系统
SEAL_FONT_FAMILY = "Seal Sans"

# 小篆相关 OpenCC 配置 -> 需要以小篆字体显示的面板
SEAL_CONFIG_PANELS = {
    "t2seal": "output",   # 繁体汉字 -> 小篆
    "s2seal": "output",   # 简体 -> 小篆
    "seal2t": "input",    # 小篆 -> 繁体汉字
}


def find_seal_font_family():
    """返回系统已安装的 Seal Sans 字体族名；未安装时返回 None（大小写不敏感匹配）"""
    for family in QFontDatabase.families():
        if SEAL_FONT_FAMILY.lower() in family.lower():
            return family
    return None


def seal_covered_mask(text, family):
    """返回与 text 等长的布尔列表，标记各字符是否被该字体收录（用于小篆字符套格式）。

    通过 QRawFont 批量查询字形索引，索引为 0（.notdef）即未收录。
    查询失败时返回全 False，调用方将按普通文本处理。
    """
    try:
        raw = QRawFont.fromFont(QFont(family))
        gids = raw.glyphIndexesForString(text)
        if len(gids) != len(text):
            return [False] * len(text)
        return [g != 0 for g in gids]
    except Exception:
        return [False] * len(text)


def utf16_positions(text):
    """返回数组 p，p[i] 为 text 前 i 个字符（code point 计）的 UTF-16 码元长度。

    QTextDocument 的光标位置按 UTF-16 码元计，而转换结果的跨度索引按
    code point 计；小篆字符位于扩展平面（每字 2 个码元），两套索引必须换算。
    """
    positions = [0] * (len(text) + 1)
    u16 = 0
    for i, ch in enumerate(text):
        positions[i] = u16
        u16 += 2 if ord(ch) > 0xFFFF else 1
    positions[len(text)] = u16
    return positions

class ConversionWorker(QThread):
    """
    转换工作线程，避免UI阻塞
    """
    progress_updated = Signal(int, str)  # 进度信号
    conversion_finished = Signal(bool, str, int, int)  # 完成信号 (success, message, success_count, total_files)
    log_message = Signal(str)  # 日志消息信号

    def __init__(self, input_path, output_folder, conversion_type='s2t', preserve_format=True,
                 convert_footnotes=True, force_encoding=None, segment_mode=None, input_paths=None,
                 custom_config_path=None, highlight_pdf=False):
        super().__init__()
        self.input_path = input_path
        self.output_folder = output_folder
        self.conversion_type = conversion_type
        self.preserve_format = preserve_format
        self.convert_footnotes = convert_footnotes
        self.force_encoding = force_encoding
        self.segment_mode = segment_mode
        self.input_paths = input_paths
        self.custom_config_path = custom_config_path
        self.highlight_pdf = highlight_pdf
        self._is_cancelled = False

    def run(self):
        try:
            # 校验自定义转换表配置（在后台线程加载，失败时给出明确错误信息）
            if self.custom_config_path:
                try:
                    OpenCC(self.custom_config_path)
                except Exception as e:
                    self.log_message.emit(f"自定义转换表配置加载失败: {e}")
                    self.conversion_finished.emit(False, f"自定义转换表配置加载失败：{e}", 0, 0)
                    return
                self.log_message.emit("自定义转换表配置加载成功")
                # 使用生成的临时配置进行转换（内含自定义内联字典）
                self.conversion_type = self.custom_config_path

            self._success_count = 0
            self._total_files = 0
            success = self.process_files()

            # 检查是否已取消
            if self._is_cancelled:
                self.conversion_finished.emit(False, "转换已被用户取消", 0, 0)
                return

            if self._total_files > 0:
                # 多文件模式
                if self._success_count == self._total_files:
                    self.conversion_finished.emit(True, "转换成功完成", self._success_count, self._total_files)
                elif self._success_count > 0:
                    self.conversion_finished.emit(False, "部分文件转换失败", self._success_count, self._total_files)
                else:
                    self.conversion_finished.emit(False, "转换过程中出现错误", 0, self._total_files)
            else:
                # 单文件模式
                if success:
                    self.conversion_finished.emit(True, "转换成功完成", 1, 1)
                else:
                    self.conversion_finished.emit(False, "转换过程中出现错误", 0, 1)
        except Exception as e:
            self.conversion_finished.emit(False, f"转换失败: {str(e)}", 0, 0)
        finally:
            self._cleanup_custom_config()

    def _cleanup_custom_config(self):
        """删除本次转换生成的临时自定义配置文件"""
        if self.custom_config_path and os.path.exists(self.custom_config_path):
            try:
                os.remove(self.custom_config_path)
            except OSError:
                pass

    def cancel(self):
        """取消转换"""
        self._is_cancelled = True
        self.log_message.emit("用户取消转换")

    def process_files(self):
        """处理文件的主要逻辑"""
        # 检查是否已取消
        if self._is_cancelled:
            return False

        self.progress_updated.emit(0, "开始处理...")

        # 检查是否已取消
        if self._is_cancelled:
            return False

        # 处理文件列表（多选文件模式）
        if self.input_paths:
            total_files = len(self.input_paths)
            self.log_message.emit(f"找到 {total_files} 个文件待处理")
            success_count = 0

            for i, file_path in enumerate(self.input_paths, 1):
                if self._is_cancelled:
                    return False

                progress = int((i / total_files) * 100)
                self.progress_updated.emit(progress, f"处理文件 {i}/{total_files}: {os.path.basename(file_path)}")

                file_ext = os.path.splitext(file_path)[1].lower()

                if file_ext == '.docx':
                    try:
                        result = convert_docx_file(
                            file_path, self.output_folder, self.conversion_type,
                            self.preserve_format,
                            self.convert_footnotes,
                            lambda msg: self.log_message.emit(msg),
                            lambda: self._is_cancelled
                        )
                        if result:
                            success_count += 1
                    except Exception as e:
                        self.log_message.emit(f"处理 {os.path.basename(file_path)} 时出错: {str(e)}")

                elif file_ext == '.txt':
                    if convert_txt_file(
                        file_path, self.output_folder, self.conversion_type,
                        lambda msg: self.log_message.emit(msg),
                        lambda: self._is_cancelled,
                        self.force_encoding,
                        self.segment_mode
                    ):
                        success_count += 1

                elif file_ext == '.md':
                    if convert_txt_file(
                        file_path, self.output_folder, self.conversion_type,
                        lambda msg: self.log_message.emit(msg),
                        lambda: self._is_cancelled
                    ):
                        success_count += 1

                elif file_ext == '.srt':
                    if convert_srt_file(
                        file_path, self.output_folder, self.conversion_type,
                        lambda msg: self.log_message.emit(msg),
                        lambda: self._is_cancelled
                    ):
                        success_count += 1

                elif file_ext in ['.ass', '.ssa']:
                    if convert_ass_file(
                        file_path, self.output_folder, self.conversion_type,
                        lambda msg: self.log_message.emit(msg),
                        lambda: self._is_cancelled
                    ):
                        success_count += 1

                elif file_ext == '.lrc':
                    if convert_lrc_file(
                        file_path, self.output_folder, self.conversion_type,
                        lambda msg: self.log_message.emit(msg),
                        lambda: self._is_cancelled
                    ):
                        success_count += 1

                elif file_ext == '.epub':
                    try:
                        if convert_epub_file(
                            file_path, self.output_folder, self.conversion_type,
                            lambda msg: self.log_message.emit(msg),
                            lambda: self._is_cancelled
                        ):
                            success_count += 1
                    except Exception as e:
                        self.log_message.emit(f"处理 {os.path.basename(file_path)} 时出错: {str(e)}")

                elif file_ext == '.pdf':
                    try:
                        if convert_pdf_file(
                            file_path, self.output_folder, self.conversion_type,
                            lambda msg: self.log_message.emit(msg),
                            lambda: self._is_cancelled,
                            self.highlight_pdf
                        ):
                            success_count += 1
                    except Exception as e:
                        self.log_message.emit(f"处理 {os.path.basename(file_path)} 时出错: {str(e)}")

                else:
                    self.log_message.emit(f"跳过不支持的文件: {os.path.basename(file_path)}")

            self.log_message.emit(f"处理完成！成功转换 {success_count}/{total_files} 个文件")
            self.progress_updated.emit(100, "转换完成!")
            self._success_count = success_count
            self._total_files = total_files
            return success_count == total_files

        # 处理单个文件
        if os.path.isfile(self.input_path):
            file_ext = os.path.splitext(self.input_path)[1].lower()

            # 检查是否已取消
            if self._is_cancelled:
                return False

            if file_ext == '.docx':
                result = convert_docx_file(
                    self.input_path, self.output_folder, self.conversion_type,
                    self.preserve_format,
                    self.convert_footnotes,
                    lambda msg: self.log_message.emit(msg),
                    lambda: self._is_cancelled
                )
                if result:
                    self.progress_updated.emit(100, "转换完成!")
                    return True
                else:
                    return False
            elif file_ext == '.txt':
                result = convert_txt_file(
                    self.input_path, self.output_folder, self.conversion_type,
                    lambda msg: self.log_message.emit(msg),
                    lambda: self._is_cancelled,
                    self.force_encoding,
                    self.segment_mode
                )
                if result:
                    self.progress_updated.emit(100, "转换完成!")
                    return True
                else:
                    return False
            elif file_ext == '.md':
                result = convert_txt_file(
                    self.input_path, self.output_folder, self.conversion_type,
                    lambda msg: self.log_message.emit(msg),
                    lambda: self._is_cancelled
                )
                if result:
                    self.progress_updated.emit(100, "转换完成!")
                    return True
                else:
                    return False
            elif file_ext == '.srt':
                result = convert_srt_file(
                    self.input_path, self.output_folder, self.conversion_type,
                    lambda msg: self.log_message.emit(msg),
                    lambda: self._is_cancelled
                )
                if result:
                    self.progress_updated.emit(100, "转换完成!")
                    return True
                else:
                    return False
            elif file_ext in ['.ass', '.ssa']:
                result = convert_ass_file(
                    self.input_path, self.output_folder, self.conversion_type,
                    lambda msg: self.log_message.emit(msg),
                    lambda: self._is_cancelled
                )
                if result:
                    self.progress_updated.emit(100, "转换完成!")
                    return True
                else:
                    return False
            elif file_ext == '.lrc':
                result = convert_lrc_file(
                    self.input_path, self.output_folder, self.conversion_type,
                    lambda msg: self.log_message.emit(msg),
                    lambda: self._is_cancelled
                )
                if result:
                    self.progress_updated.emit(100, "转换完成!")
                    return True
                else:
                    return False
            elif file_ext == '.epub':
                result = convert_epub_file(
                    self.input_path, self.output_folder, self.conversion_type,
                    lambda msg: self.log_message.emit(msg),
                    lambda: self._is_cancelled
                )
                if result:
                    self.progress_updated.emit(100, "转换完成!")
                    return True
                else:
                    return False
            elif file_ext == '.pdf':
                try:
                    result = convert_pdf_file(
                        self.input_path, self.output_folder, self.conversion_type,
                        lambda msg: self.log_message.emit(msg),
                        lambda: self._is_cancelled,
                        self.highlight_pdf
                    )
                except Exception as e:
                    self.log_message.emit(f"处理 {os.path.basename(self.input_path)} 时出错: {str(e)}")
                    result = False
                if result:
                    self.progress_updated.emit(100, "转换完成!")
                    return True
                else:
                    return False
            else:
                self.log_message.emit("错误：不支持的文件格式，仅支持docx、txt、md、srt、ass、ssa、lrc、epub、pdf文件")
                return False

        # 处理文件夹
        elif os.path.isdir(self.input_path):
            # 获取所有支持的文件
            supported_files = []
            for f in os.listdir(self.input_path):
                # 检查是否已取消
                if self._is_cancelled:
                    return False

                file_ext = os.path.splitext(f)[1].lower()
                if file_ext in ['.docx', '.txt', '.md', '.srt', '.ass', '.ssa', '.lrc', '.epub', '.pdf']:
                    supported_files.append(f)

            if not supported_files:
                self.log_message.emit("在指定文件夹中未找到支持的docx、txt、md、srt、ass、ssa、lrc、epub、pdf文件")
                return False

            self.log_message.emit(f"找到 {len(supported_files)} 个文件待处理")

            success_count = 0
            total_files = len(supported_files)

            for i, filename in enumerate(supported_files, 1):
                # 检查是否已取消
                if self._is_cancelled:
                    return False

                progress = int((i / total_files) * 100)
                self.progress_updated.emit(progress, f"处理文件 {i}/{total_files}: {filename}")

                file_path = os.path.join(self.input_path, filename)
                file_ext = os.path.splitext(filename)[1].lower()

                if file_ext == '.docx':
                    # 对于docx文件，使用转换器类
                    try:
                        result = convert_docx_file(
                            file_path, self.output_folder, self.conversion_type,
                            self.preserve_format,
                            self.convert_footnotes,
                            lambda msg: self.log_message.emit(msg),
                            lambda: self._is_cancelled
                        )
                        if result:
                            success_count += 1

                    except Exception as e:
                        self.log_message.emit(f"处理 {filename} 时出错: {str(e)}")

                elif file_ext == '.txt':
                    if convert_txt_file(
                        file_path, self.output_folder, self.conversion_type,
                        lambda msg: self.log_message.emit(msg),
                        lambda: self._is_cancelled,
                        self.force_encoding,
                        self.segment_mode
                    ):
                        success_count += 1

                elif file_ext == '.md':
                    if convert_txt_file(
                        file_path, self.output_folder, self.conversion_type,
                        lambda msg: self.log_message.emit(msg),
                        lambda: self._is_cancelled
                    ):
                        success_count += 1

                elif file_ext == '.srt':
                    if convert_srt_file(
                        file_path, self.output_folder, self.conversion_type,
                        lambda msg: self.log_message.emit(msg),
                        lambda: self._is_cancelled
                    ):
                        success_count += 1

                elif file_ext in ['.ass', '.ssa']:
                    if convert_ass_file(
                        file_path, self.output_folder, self.conversion_type,
                        lambda msg: self.log_message.emit(msg),
                        lambda: self._is_cancelled
                    ):
                        success_count += 1

                elif file_ext == '.lrc':
                    if convert_lrc_file(
                        file_path, self.output_folder, self.conversion_type,
                        lambda msg: self.log_message.emit(msg),
                        lambda: self._is_cancelled
                    ):
                        success_count += 1
                elif file_ext == '.epub':
                    try:
                        if convert_epub_file(
                            file_path, self.output_folder, self.conversion_type,
                            lambda msg: self.log_message.emit(msg),
                            lambda: self._is_cancelled
                        ):
                            success_count += 1
                    except Exception as e:
                        self.log_message.emit(f"处理 {filename} 时出错: {str(e)}")

                elif file_ext == '.pdf':
                    try:
                        if convert_pdf_file(
                            file_path, self.output_folder, self.conversion_type,
                            lambda msg: self.log_message.emit(msg),
                            lambda: self._is_cancelled,
                            self.highlight_pdf
                        ):
                            success_count += 1
                    except Exception as e:
                        self.log_message.emit(f"处理 {filename} 时出错: {str(e)}")

            self.log_message.emit(f"处理完成！成功转换 {success_count}/{total_files} 个文件")
            self.progress_updated.emit(100, "转换完成!")
            self._success_count = success_count
            self._total_files = total_files
            return success_count == total_files
        else:
            self.log_message.emit("错误：输入的路径既不是有效的文件也不是文件夹")
            return False


class TextConversionWorker(QThread):
    """
    文字转换工作线程（带一对多歧义标注），避免转换时阻塞UI
    """
    conversion_done = Signal(dict)  # 完成信号（转换结果：output/defs/spans/stats）
    conversion_failed = Signal(str)  # 失败信号（错误信息）

    def __init__(self, text, config, custom_config_path=None):
        super().__init__()
        self.text = text
        self.config = config
        self.custom_config_path = custom_config_path
        self._proc = None
        self._cancelled = False

    def cancel(self):
        """取消转换（终止 OpenCC 子进程）"""
        self._cancelled = True
        if self._proc is not None:
            try:
                self._proc.terminate()
            except OSError:
                pass

    def run(self):
        try:
            result = convert_with_ambiguities(
                self.text, self.config,
                on_spawn=lambda proc: setattr(self, "_proc", proc)
            )
            self.conversion_done.emit(result)
        except AmbiguityConversionError as e:
            self.conversion_failed.emit("转换已取消" if self._cancelled else str(e))
        except Exception as e:
            self.conversion_failed.emit(f"转换失败：{e}")
        finally:
            if self.custom_config_path and os.path.exists(self.custom_config_path):
                try:
                    os.remove(self.custom_config_path)
                except OSError:
                    pass


class AmbiguityOutputEdit(QTextEdit):
    """
    转换结果输出框：一对多歧义跨度自绘红色波浪线（比内置下划线更粗），
    点击波浪线弹出候选字菜单
    """
    ambiguity_clicked = Signal(int, object)  # (歧义跨度序号, 弹出菜单的全局坐标 QPoint)

    WAVE_COLOR = QColor("#e11d48")
    WAVE_PEN_WIDTH = 2.0  # 波浪线粗细（内置下划线无法调粗，故自绘）
    WAVE_AMPLITUDE = 2.0  # 波幅（像素）
    WAVE_HALF_WAVE = 4.0  # 半个波浪的长度（像素）

    def __init__(self):
        super().__init__()
        self.setReadOnly(True)
        self._spans = []  # [{"start", "length", "def_idx", "resolved"}]（code point 索引）
        self._mark_ambiguities = False
        self._seal_format = None   # 小篆字符格式（None 表示普通显示）
        self._u16_map = [0]        # code point 位置 -> UTF-16 码元位置

    def display_result(self, text, spans, mark_ambiguities, seal_format=None):
        """显示转换结果；mark_ambiguities 为真时给歧义跨度加红色波浪线。

        seal_format 不为 None 时，Seal Sans 已收录的字符用该格式渲染——
        Qt 尚未收录 Unicode 18.0 篆书区块的覆盖数据，若仅设置面板字体，
        文本分项会把小篆字符交给回退字体而显示为方框，故必须按字符显式指定。
        """
        self._spans = [dict(span, resolved=False) for span in spans]
        self._mark_ambiguities = mark_ambiguities
        self._seal_format = seal_format
        self.setPlainText(text)
        self._rebuild_u16_map()
        if seal_format is not None:
            self._apply_seal_format(text)
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.Start)
        self.setTextCursor(cursor)

    def _apply_seal_format(self, text):
        """给 Seal Sans 已收录的连续字符段套小篆格式"""
        mask = seal_covered_mask(text, self._seal_format.font().family())
        cursor = QTextCursor(self.document())
        i, n = 0, len(text)
        while i < n:
            if not mask[i]:
                i += 1
                continue
            j = i + 1
            while j < n and mask[j]:
                j += 1
            cursor.setPosition(self._u16_map[i])
            cursor.setPosition(self._u16_map[j], QTextCursor.KeepAnchor)
            cursor.setCharFormat(self._seal_format)
            i = j

    def _rebuild_u16_map(self):
        """文本变化后重建 code point -> UTF-16 位置映射"""
        self._u16_map = utf16_positions(self.toPlainText())

    def _u16(self, cp_pos):
        """code point 位置 -> QTextCursor 位置（UTF-16 码元）"""
        return self._u16_map[cp_pos]

    def _cp(self, u16_pos):
        """QTextCursor 位置（UTF-16 码元）-> code point 位置"""
        return bisect.bisect_right(self._u16_map, u16_pos) - 1

    def spans(self):
        return self._spans

    def span_text(self, span_idx):
        span = self._spans[span_idx]
        return self.toPlainText()[span["start"]:span["start"] + span["length"]]

    def _span_at(self, pos):
        """返回坐标 pos 处的歧义跨度序号；不在任何跨度内返回 None"""
        position = self._cp(self.cursorForPosition(pos).position())
        for i, span in enumerate(self._spans):
            if span["start"] <= position < span["start"] + span["length"]:
                return i
        # 点击落在跨度最后一个字符的右边界时 position 恰为 end，回退一位再试
        if position > 0:
            position -= 1
            for i, span in enumerate(self._spans):
                if span["start"] <= position < span["start"] + span["length"]:
                    return i
        return None

    def mouseReleaseEvent(self, event):
        if (event.button() == Qt.LeftButton
                and event.modifiers() == Qt.NoModifier
                and not self.textCursor().hasSelection()):
            span_idx = self._span_at(event.position().toPoint())
            if span_idx is not None and not self._spans[span_idx]["resolved"]:
                self.ambiguity_clicked.emit(
                    span_idx, event.globalPosition().toPoint())
                return
        super().mouseReleaseEvent(event)

    def apply_candidate(self, span_idx, candidate):
        """用选中的候选值替换对应歧义跨度的文本，并调整后续跨度偏移"""
        span = self._spans[span_idx]
        cursor = QTextCursor(self.document())
        cursor.setPosition(self._u16(span["start"]))
        cursor.setPosition(self._u16(span["start"] + span["length"]), QTextCursor.KeepAnchor)
        cursor.removeSelectedText()
        cursor.insertText(candidate, self._seal_format or QTextCharFormat())
        delta = len(candidate) - span["length"]
        span["length"] = len(candidate)
        span["resolved"] = True
        for other in self._spans[span_idx + 1:]:
            other["start"] += delta
        self._rebuild_u16_map()

    def paintEvent(self, event):
        super().paintEvent(event)
        if not (self._mark_ambiguities and self._spans):
            return
        painter = QPainter(self.viewport())
        painter.setRenderHint(QPainter.Antialiasing)
        pen = QPen(self.WAVE_COLOR, self.WAVE_PEN_WIDTH)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)
        text = self.toPlainText()
        for span in self._spans:
            if span["resolved"]:
                continue
            self._draw_span_wave(painter, span, text)
        painter.end()

    def _draw_span_wave(self, painter, span, text):
        """在跨度下方绘制波浪线；逐字符取位置，跨行（自动换行）时分行绘制"""
        viewport = self.viewport()
        visible_top, visible_bottom = -40, viewport.height() + 40
        run_x1 = run_x2 = run_y = None
        for pos in range(span["start"], span["start"] + span["length"]):
            cursor = QTextCursor(self.document())
            cursor.setPosition(self._u16(pos))
            rect = self.cursorRect(cursor)
            if run_y is not None and (rect.bottom() < visible_top or rect.top() > visible_bottom):
                break
            # 字符宽度按其实际渲染字体度量（小篆字符套用了 Seal Sans 格式）
            fmt_font = cursor.charFormat().font()
            metrics = (QFontMetricsF(fmt_font) if fmt_font.family()
                       else QFontMetricsF(self.font()))
            char_width = metrics.horizontalAdvance(text[pos]) if pos < len(text) else rect.height()
            if run_y is not None and abs(rect.bottom() - run_y) <= 1:
                # 同一行，延伸当前波浪段
                run_x2 = rect.x() + char_width
            else:
                # 新的一行（或首个字符）：先画完上一段
                if run_y is not None:
                    self._paint_wave_segment(painter, run_x1, run_x2, run_y)
                run_x1, run_x2 = rect.x(), rect.x() + char_width
                run_y = rect.bottom() - 2
        if run_y is not None:
            self._paint_wave_segment(painter, run_x1, run_x2, run_y)

    def _paint_wave_segment(self, painter, x1, x2, y):
        """绘制 [x1, x2] 区间、基线为 y 的一段波浪线"""
        if x2 <= x1:
            return
        path = QPainterPath()
        path.moveTo(x1, y)
        x, up = x1, True
        while x < x2:
            next_x = min(x + self.WAVE_HALF_WAVE, x2)
            ctrl_y = y - self.WAVE_AMPLITUDE if up else y + self.WAVE_AMPLITUDE
            path.quadTo((x + next_x) / 2, ctrl_y, next_x, y)
            x, up = next_x, not up
        painter.drawPath(path)


class ModernUI(QMainWindow):
    def __init__(self):
        super().__init__()

        # 初始化设置存储
        self.settings = QSettings("TraditionalConverter", "AppSettings")

        # 从设置中加载主题，如果不存在则使用默认的暗色主题
        saved_theme = self.settings.value("theme", "dark")
        self.current_theme = saved_theme

        # 存储多选文件列表
        self.selected_files = []

        self.init_ui()

    def init_ui(self):
        # 设置窗口属性
        self.setWindowTitle("Oh My OpenCC")
        self.setGeometry(100, 100, 1050, 750)
        self.setMinimumSize(900, 650)

        # 设置窗口图标 - 新增的logo功能
        self.setWindowIcon(QIcon(self.get_logo_path()))

        # 应用默认主题
        self.apply_theme(self.current_theme)

        # 创建主窗口部件
        main_widget = QWidget()
        self.setCentralWidget(main_widget)

        # 创建主布局（左右分区）
        main_layout = QHBoxLayout(main_widget)
        main_layout.setSpacing(0)
        main_layout.setContentsMargins(0, 0, 0, 0)

        # 左侧导航栏
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(240)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(15, 20, 15, 20)
        sidebar_layout.setSpacing(15)

        # 标题区域
        title_label = QLabel("简繁通转换大师")
        title_label.setAlignment(Qt.AlignCenter)
        title_label.setObjectName("titleLabel")
        sidebar_layout.addWidget(title_label)

        # 导航按钮
        self.nav_buttons = []
        nav_items = ["文件转换", "文字转换", "设置", "关于"]
        for index, name in enumerate(nav_items):
            btn = QPushButton(name)
            btn.setObjectName("navButton")
            btn.setCheckable(True)
            btn.clicked.connect(lambda checked, idx=index: self.switch_page(idx))
            sidebar_layout.addWidget(btn)
            self.nav_buttons.append(btn)
        self.nav_buttons[0].setChecked(True)

        sidebar_layout.addStretch()
        main_layout.addWidget(sidebar)

        # 右侧内容区域
        self.content_stack = QStackedWidget()
        self.content_stack.addWidget(self.create_conversion_tab())
        self.content_stack.addWidget(self.create_text_tab())
        self.content_stack.addWidget(self.create_settings_tab())
        self.content_stack.addWidget(self.create_about_tab())
        main_layout.addWidget(self.content_stack)

        # 状态栏
        self.statusBar().showMessage("就绪")

    def switch_page(self, index):
        """切换右侧内容页面"""
        self.content_stack.setCurrentIndex(index)
        for i, btn in enumerate(self.nav_buttons):
            btn.setChecked(i == index)
        page_names = ["文件转换", "文字转换", "设置", "关于"]
        self.statusBar().showMessage(f"当前页面: {page_names[index]}")

    def get_logo_path(self):
        """
        获取logo文件的路径
        程序会按以下顺序查找logo文件：
        1. 与程序同目录下的"logo.ico"
        2. 与程序同目录下的"logo.png"
        3. 程序内部资源（如果没有外部文件，则返回空）
        """
        # 尝试查找logo.ico文件
        logo_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logo.ico")
        if os.path.exists(logo_path):
            return logo_path

        # 尝试查找logo.png文件
        logo_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logo.png")
        if os.path.exists(logo_path):
            return logo_path

        # 如果没有找到外部文件，可以创建一个临时的logo
        # 这里我们创建一个简单的程序内建图标作为fallback
        return ""

    def create_settings_tab(self):
        """创建设置选项卡"""
        tab = QWidget()
        outer_layout = QVBoxLayout(tab)
        outer_layout.setContentsMargins(0, 0, 0, 0)

        # 设置项较多时允许滚动，避免内容被裁剪
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        outer_layout.addWidget(scroll)

        inner = QWidget()
        scroll.setWidget(inner)
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(20)

        # 主题设置区域
        theme_group = QGroupBox("主题设置")
        theme_layout = QVBoxLayout(theme_group)
        theme_layout.setSpacing(15)
        theme_layout.setContentsMargins(15, 20, 15, 15)

        # 主题选择说明
        theme_label = QLabel("选择您喜欢的界面主题:")
        theme_layout.addWidget(theme_label)

        # 主题选择按钮
        theme_button_layout = QHBoxLayout()

        # 创建复选框（互斥：勾选一个自动取消另一个）
        self.dark_theme_cb = QCheckBox("暗色主题")
        self.light_theme_cb = QCheckBox("浅色主题")

        # 根据保存的主题设置默认选中
        if self.current_theme == "dark":
            self.dark_theme_cb.setChecked(True)
        else:
            self.light_theme_cb.setChecked(True)

        # 将复选框添加到布局
        theme_button_layout.addWidget(self.dark_theme_cb)
        theme_button_layout.addWidget(self.light_theme_cb)
        theme_button_layout.addStretch()

        theme_layout.addLayout(theme_button_layout)

        # 连接信号：勾选一个时自动取消另一个（互斥行为）
        self.dark_theme_cb.stateChanged.connect(lambda state: self.on_theme_changed("dark", state))
        self.light_theme_cb.stateChanged.connect(lambda state: self.on_theme_changed("light", state))

        layout.addWidget(theme_group)

        # 文件编码检测设置区域
        encoding_group = QGroupBox("编码检测设置")
        encoding_group_layout = QVBoxLayout(encoding_group)
        encoding_group_layout.setSpacing(15)
        encoding_group_layout.setContentsMargins(15, 20, 15, 15)

        # 编码检测说明
        encoding_desc = QLabel(
            "指定读取TXT等文本文件时使用的编码，默认为自动检测。\n"
            "如果自动检测识别错误（例如Big5编码被误识别为GB18030），\n"
            "可在此手动强制指定编码。"
        )
        encoding_desc.setWordWrap(True)
        encoding_group_layout.addWidget(encoding_desc)

        # 编码选择
        encoding_select_layout = QHBoxLayout()
        encoding_select_layout.setSpacing(10)
        encoding_select_layout.addWidget(QLabel("强制编码:"))

        self.encoding_combo = QComboBox()
        self.encoding_combo.addItem("自动检测", None)
        self.encoding_combo.addItem("强制使用 GB18030", "gb18030")
        self.encoding_combo.addItem("强制使用 GBK", "gbk")
        self.encoding_combo.addItem("强制使用 GB2312", "gb2312")
        self.encoding_combo.addItem("强制使用 UTF-8", "utf-8")
        self.encoding_combo.addItem("强制使用 Big5", "cp950")
        self.encoding_combo.addItem("强制使用 Big5-HKSCS", "big5-hkscs")
        self.encoding_combo.setToolTip(
            "对于TXT等文本文件，指定读取时使用的编码。\n"
            "默认为自动检测；如果自动检测识别错误，可手动强制指定。"
        )

        # 从设置中恢复编码选择
        saved_encoding_index = self.settings.value("encoding_index", 0, type=int)
        if 0 <= saved_encoding_index < self.encoding_combo.count():
            self.encoding_combo.setCurrentIndex(saved_encoding_index)

        self.encoding_combo.currentIndexChanged.connect(self.on_encoding_changed)
        encoding_select_layout.addWidget(self.encoding_combo)
        encoding_group_layout.addLayout(encoding_select_layout)

        layout.addWidget(encoding_group)

        # 分词设置区域
        segment_group = QGroupBox("分词设置")
        segment_layout = QVBoxLayout(segment_group)
        segment_layout.setSpacing(15)
        segment_layout.setContentsMargins(15, 20, 15, 15)

        # 分词说明
        segment_desc = QLabel(
            "分词功能可以在转换前对文本进行分词预处理，以提高转换准确性。"
        )
        segment_desc.setWordWrap(True)
        segment_layout.addWidget(segment_desc)

        # 分词选项
        segment_options_layout = QVBoxLayout()

        # 不分词选项
        self.no_segment_cb = QCheckBox("不分词（默认）")
        self.no_segment_cb.setChecked(True)
        self.no_segment_cb.stateChanged.connect(lambda state: self.on_segment_mode_changed("none", state))

        # 结巴分词（现代汉语）选项
        self.jieba_modern_cb = QCheckBox("转换前使用结巴分词进行文本分词预处理")
        self.jieba_modern_cb.stateChanged.connect(lambda state: self.on_segment_mode_changed("jieba_modern", state))

        # 结巴分词（古汉语）选项
        self.jieba_ancient_cb = QCheckBox("转换前使用结巴分词对古汉语文本进行分词预处理")
        self.jieba_ancient_cb.stateChanged.connect(lambda state: self.on_segment_mode_changed("jieba_ancient", state))

        segment_options_layout.addWidget(self.no_segment_cb)
        segment_options_layout.addWidget(self.jieba_modern_cb)
        segment_options_layout.addWidget(self.jieba_ancient_cb)
        segment_layout.addLayout(segment_options_layout)

        # 从设置中恢复分词选择
        saved_segment_mode = self.settings.value("segment_mode", "none")
        self._apply_segment_mode(saved_segment_mode)

        layout.addWidget(segment_group)

        # 自定义转换表设置区域
        custom_group = QGroupBox("自定义转换表")
        custom_layout = QVBoxLayout(custom_group)
        custom_layout.setSpacing(12)
        custom_layout.setContentsMargins(15, 20, 15, 15)

        # 自定义转换表说明
        custom_desc = QLabel(
            "每一行一条规则，格式：原词→目标词（也支持 =、=> 或 Tab 分隔）。\n"
            "以 # 或 // 开头的行视为注释，空行自动忽略。\n"
            "注意：规则中的原词不可重复，否则无法加载。"
        )
        custom_desc.setWordWrap(True)
        custom_layout.addWidget(custom_desc)

        self.custom_dict_cb = QCheckBox("启用自定义转换表")
        self.custom_dict_cb.stateChanged.connect(self.on_custom_dict_enabled_changed)
        custom_layout.addWidget(self.custom_dict_cb)

        # 应用转换类型选择（单选，指定本转换表作用于哪个转换类型）
        apply_type_layout = QHBoxLayout()
        apply_type_layout.setSpacing(10)
        apply_type_layout.addWidget(QLabel("应用转换类型:"))
        self.custom_dict_type_combo = QComboBox()
        for display_name, config_name in CONVERSION_TYPES.items():
            self.custom_dict_type_combo.addItem(display_name, config_name)
        self.custom_dict_type_combo.setToolTip(
            "自定义转换表仅在该转换类型下生效；\n"
            "使用其他转换类型转换时，本表不会参与。"
        )
        self.custom_dict_type_combo.currentIndexChanged.connect(self.on_custom_dict_type_changed)
        apply_type_layout.addWidget(self.custom_dict_type_combo, 1)
        apply_type_layout.addStretch()
        custom_layout.addLayout(apply_type_layout)

        self.custom_dict_edit = QPlainTextEdit()
        self.custom_dict_edit.setPlaceholderText(
            "每一行一条规则：原词→目标词\n例如：服务器→伺服器"
        )
        self.custom_dict_edit.setMaximumHeight(180)
        self.custom_dict_edit.textChanged.connect(self.on_custom_dict_text_changed)
        custom_layout.addWidget(self.custom_dict_edit)

        custom_btn_layout = QHBoxLayout()
        import_btn = QPushButton("从文件导入")
        import_btn.setObjectName("browseButton")
        import_btn.clicked.connect(self.import_custom_dict)
        export_btn = QPushButton("导出到文件")
        export_btn.setObjectName("browseButton")
        export_btn.clicked.connect(self.export_custom_dict)
        custom_btn_layout.addWidget(import_btn)
        custom_btn_layout.addWidget(export_btn)
        custom_btn_layout.addStretch()
        custom_layout.addLayout(custom_btn_layout)

        # 从设置中恢复自定义转换表（默认关闭，编辑器默认只显示注释示例）
        self.custom_dict_cb.setChecked(self.settings.value("custom_dict_enabled", False, type=bool))
        saved_apply_type = self.settings.value("custom_dict_apply_type", "s2t")
        apply_index = self.custom_dict_type_combo.findData(saved_apply_type)
        if apply_index >= 0:
            self.custom_dict_type_combo.setCurrentIndex(apply_index)
        saved_entries = self.settings.value("custom_dict_entries", "")
        if saved_entries:
            self.custom_dict_edit.setPlainText(saved_entries)
        else:
            self.custom_dict_edit.setPlainText(
                "# 示例（删除“# ”后方可生效）：\n"
                "# 服务器→伺服器\n"
                "# 麦旋风→冰炫風\n"
            )

        layout.addWidget(custom_group)

        # PDF校对模式设置区域
        pdf_group = QGroupBox("PDF校对模式")
        pdf_group_layout = QVBoxLayout(pdf_group)
        pdf_group_layout.setSpacing(15)
        pdf_group_layout.setContentsMargins(15, 20, 15, 15)

        # 校对模式说明
        pdf_desc = QLabel(
            "勾选后，PDF转换输出的文档中被转换修改的汉字将以黄色底纹标出，便于校对。"
        )
        pdf_desc.setWordWrap(True)
        pdf_group_layout.addWidget(pdf_desc)

        self.highlight_pdf_cb = QCheckBox("PDF转换后为被修改的字添加黄色高亮")
        self.highlight_pdf_cb.setToolTip("仅对PDF文件生效：转换中被修改的汉字以黄色底纹标出")
        self.highlight_pdf_cb.stateChanged.connect(self.on_pdf_highlight_changed)
        pdf_group_layout.addWidget(self.highlight_pdf_cb)

        # 从设置中恢复PDF校对模式
        saved_pdf_highlight = self.settings.value("pdf_highlight", False, type=bool)
        self.highlight_pdf_cb.setChecked(saved_pdf_highlight)

        layout.addWidget(pdf_group)

        layout.addStretch()
        return tab

    def _apply_segment_mode(self, mode):
        """应用分词模式设置"""
        self.no_segment_cb.setChecked(False)
        self.jieba_modern_cb.setChecked(False)
        self.jieba_ancient_cb.setChecked(False)

        if mode == "jieba_modern":
            self.jieba_modern_cb.setChecked(True)
        elif mode == "jieba_ancient":
            self.jieba_ancient_cb.setChecked(True)
        else:
            self.no_segment_cb.setChecked(True)

    def on_segment_mode_changed(self, mode, state):
        """分词模式更改事件处理（互斥逻辑）"""
        if state != Qt.CheckState.Checked.value:
            return

        # 互斥：勾选一个时取消其他
        if mode == "none":
            self.jieba_modern_cb.setChecked(False)
            self.jieba_ancient_cb.setChecked(False)
        elif mode == "jieba_modern":
            self.no_segment_cb.setChecked(False)
            self.jieba_ancient_cb.setChecked(False)
        elif mode == "jieba_ancient":
            self.no_segment_cb.setChecked(False)
            self.jieba_modern_cb.setChecked(False)

        # 保存设置
        self.settings.setValue("segment_mode", mode)
        mode_display = {
            "none": "不分词",
            "jieba_modern": "结巴分词（现代汉语）",
            "jieba_ancient": "结巴分词（古汉语）"
        }
        self.statusBar().showMessage(f"分词设置已更改为: {mode_display.get(mode, '不分词')}")

    def on_encoding_changed(self, index):
        """编码设置更改事件处理"""
        self.settings.setValue("encoding_index", index)
        encoding_name = self.encoding_combo.currentText()
        self.statusBar().showMessage(f"编码设置已更改为: {encoding_name}")

    def on_custom_dict_enabled_changed(self, state):
        """自定义转换表启用开关更改事件处理"""
        enabled = state == Qt.CheckState.Checked.value
        self.settings.setValue("custom_dict_enabled", enabled)
        self.statusBar().showMessage("自定义转换表已启用" if enabled else "自定义转换表已禁用")

    def on_custom_dict_text_changed(self):
        """自定义转换表内容更改事件处理"""
        self.settings.setValue("custom_dict_entries", self.custom_dict_edit.toPlainText())

    def on_custom_dict_type_changed(self, index):
        """自定义转换表应用类型更改事件处理"""
        config_name = self.custom_dict_type_combo.itemData(index)
        self.settings.setValue("custom_dict_apply_type", config_name)
        self.statusBar().showMessage(f"自定义转换表将应用于: {self.custom_dict_type_combo.currentText()}")

    def import_custom_dict(self):
        """从文件导入自定义转换表"""
        path, _ = QFileDialog.getOpenFileName(
            self, "导入自定义转换表", "",
            "文本文件 (*.txt *.csv *.json);;所有文件 (*)"
        )
        if not path:
            return
        for encoding in ('utf-8-sig', 'gb18030'):
            try:
                with open(path, 'r', encoding=encoding) as f:
                    content = f.read()
                break
            except (UnicodeDecodeError, UnicodeError):
                continue
        else:
            QMessageBox.warning(self, "导入失败", "无法读取文件内容（编码不受支持）")
            return
        self.custom_dict_edit.setPlainText(content)
        self.statusBar().showMessage(f"已从文件导入自定义转换表: {os.path.basename(path)}")

    def export_custom_dict(self):
        """将自定义转换表导出到文件"""
        path, _ = QFileDialog.getSaveFileName(
            self, "导出自定义转换表", "custom_dict.txt",
            "文本文件 (*.txt);;所有文件 (*)"
        )
        if not path:
            return
        try:
            with open(path, 'w', encoding='utf-8') as f:
                f.write(self.custom_dict_edit.toPlainText())
        except Exception as e:
            QMessageBox.critical(self, "导出失败", f"保存文件失败：{e}")
            return
        self.statusBar().showMessage(f"自定义转换表已导出至: {path}")

    def on_pdf_highlight_changed(self, state):
        """PDF校对模式（修改字黄色高亮）更改事件处理"""
        checked = self.highlight_pdf_cb.isChecked()
        self.settings.setValue("pdf_highlight", checked)
        self.statusBar().showMessage(
            f"PDF校对模式（修改字黄色高亮）已{'开启' if checked else '关闭'}")
        
    def on_theme_changed(self, theme, state):
        """主题更改事件处理（复选框互斥逻辑）"""
        if state != Qt.CheckState.Checked.value:
            return
        # 互斥：勾选一个时取消另一个
        if theme == "dark":
            self.light_theme_cb.setChecked(False)
        else:
            self.dark_theme_cb.setChecked(False)
        self.change_theme(theme)

    def apply_theme(self, theme):
        """应用指定主题"""
        if theme == "dark":
            self.apply_dark_theme()
        else:
            self.apply_light_theme()

    def apply_dark_theme(self):
        """应用暗色主题"""
        self.current_theme = "dark"

        # 设置暗色调色板
        dark_palette = QPalette()
        dark_palette.setColor(QPalette.Window, QColor(43, 43, 43))
        dark_palette.setColor(QPalette.WindowText, Qt.white)
        dark_palette.setColor(QPalette.Base, QColor(30, 30, 30))
        dark_palette.setColor(QPalette.AlternateBase, QColor(43, 43, 43))
        dark_palette.setColor(QPalette.ToolTipBase, Qt.white)
        dark_palette.setColor(QPalette.ToolTipText, Qt.black)
        dark_palette.setColor(QPalette.Text, Qt.white)
        dark_palette.setColor(QPalette.Button, QColor(43, 43, 43))
        dark_palette.setColor(QPalette.ButtonText, Qt.white)
        dark_palette.setColor(QPalette.BrightText, Qt.red)
        dark_palette.setColor(QPalette.Link, QColor(42, 130, 218))
        dark_palette.setColor(QPalette.Highlight, QColor(42, 130, 218))
        dark_palette.setColor(QPalette.HighlightedText, Qt.black)
        QApplication.instance().setPalette(dark_palette)

        # 设置暗色样式表
        self.setStyleSheet("""
            QMainWindow {
                background-color: #2b2b2b;
            }
            QWidget {
                background-color: #2b2b2b;
                color: #ffffff;
                font-family: "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
            }
            QPushButton {
                background-color: #375a7f;
                border: none;
                color: white;
                padding: 10px;
                font-size: 14px;
                border-radius: 5px;
                min-width: 100px;
            }
            QPushButton:hover {
                background-color: #4a77a8;
            }
            QPushButton:pressed {
                background-color: #2c4a69;
            }
            QPushButton#startButton {
                background-color: #00bc8c;
                font-weight: bold;
                padding: 12px;
                font-size: 16px;
            }
            QPushButton#startButton:hover {
                background-color: #00e6ac;
            }
            QPushButton#browseButton {
                background-color: #3498db;
            }
            QPushButton#browseButton:hover {
                background-color: #5dade2;
            }
            QLineEdit, QTextEdit {
                background-color: #3c3c3c;
                border: 1px solid #555555;
                color: #ffffff;
                padding: 8px;
                border-radius: 4px;
            }
            QGroupBox {
                border: 1px solid #555555;
                border-radius: 5px;
                margin-top: 1ex;
                font-weight: bold;
                color: #3498db;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px 0 5px;
                color: #3498db;
            }
            QProgressBar {
                border: 1px solid #555555;
                border-radius: 5px;
                text-align: center;
                height: 20px;
                color: white;
            }
            QProgressBar::chunk {
                background-color: #00bc8c;
                width: 20px;
            }
            QComboBox {
                background-color: #3c3c3c;
                border: 1px solid #555555;
                color: #ffffff;
                padding: 5px;
                border-radius: 4px;
            }
            QComboBox::drop-down {
                border: none;
            }
            QComboBox QAbstractItemView {
                background-color: #3c3c3c;
                color: white;
            }
            QLabel {
                color: #aaaaaa;
            }
            QLabel#titleLabel {
                font-size: 24px;
                font-weight: bold;
                color: #00bc8c;
                margin: 10px;
            }
            QListWidget {
                background-color: #3c3c3c;
                border: 1px solid #555555;
                color: #ffffff;
            }
            QCheckBox:disabled {
                color: #777777;
            }
            QFrame#sidebar {
                background-color: #252525;
                border-right: 1px solid #555555;
            }
            QPushButton#navButton {
                background-color: #3c3c3c;
                border: none;
                color: #aaaaaa;
                padding: 12px 16px;
                font-size: 14px;
                border-radius: 5px;
                text-align: left;
                margin: 4px 0px;
            }
            QPushButton#navButton:hover {
                background-color: #4a4a4a;
                color: #ffffff;
            }
            QPushButton#navButton:checked {
                background-color: #375a7f;
                color: #ffffff;
                font-weight: bold;
            }

            QPushButton#cancelButton {
                background-color: #e74c3c;
                font-weight: bold;
                padding: 12px;
                font-size: 16px;
            }
            QPushButton#cancelButton:hover {
                background-color: #c0392b;
            }
        """)

    def apply_light_theme(self):
        """应用浅色主题"""
        self.current_theme = "light"

        # 设置浅色调色板
        light_palette = QPalette()
        light_palette.setColor(QPalette.Window, QColor(240, 240, 240))
        light_palette.setColor(QPalette.WindowText, Qt.black)
        light_palette.setColor(QPalette.Base, Qt.white)
        light_palette.setColor(QPalette.AlternateBase, QColor(245, 245, 245))
        light_palette.setColor(QPalette.ToolTipBase, Qt.white)
        light_palette.setColor(QPalette.ToolTipText, Qt.black)
        light_palette.setColor(QPalette.Text, Qt.black)
        light_palette.setColor(QPalette.Button, QColor(240, 240, 240))
        light_palette.setColor(QPalette.ButtonText, Qt.black)
        light_palette.setColor(QPalette.BrightText, Qt.red)
        light_palette.setColor(QPalette.Link, QColor(0, 120, 215))
        light_palette.setColor(QPalette.Highlight, QColor(0, 120, 215))
        light_palette.setColor(QPalette.HighlightedText, Qt.white)
        QApplication.instance().setPalette(light_palette)

        # 设置浅色样式表
        self.setStyleSheet("""
            QMainWindow {
                background-color: #f0f0f0;
            }
            QWidget {
                background-color: #f0f0f0;
                color: #333333;
                font-family: "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
            }
            QPushButton {
                background-color: #4a86e8;
                border: none;
                color: white;
                padding: 10px;
                font-size: 14px;
                border-radius: 5px;
                min-width: 100px;
            }
            QPushButton:hover {
                background-color: #6a9ce8;
            }
            QPushButton:pressed {
                background-color: #3a76c8;
            }
            QPushButton#startButton {
                background-color: #4caf50;
                font-weight: bold;
                padding: 12px;
                font-size: 16px;
            }
            QPushButton#startButton:hover {
                background-color: #66bb6a;
            }
            QPushButton#browseButton {
                background-color: #2196f3;
            }
            QPushButton#browseButton:hover {
                background-color: #42a5f5;
            }
            QLineEdit, QTextEdit {
                background-color: white;
                border: 1px solid #cccccc;
                color: #333333;
                padding: 8px;
                border-radius: 4px;
            }
            QGroupBox {
                border: 1px solid #cccccc;
                border-radius: 5px;
                margin-top: 1ex;
                font-weight: bold;
                color: #555555;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px 0 5px;
                color: #555555;
            }
            QProgressBar {
                border: 1px solid #cccccc;
                border-radius: 5px;
                text-align: center;
                height: 20px;
                color: #333333;
            }
            QProgressBar::chunk {
                background-color: #4caf50;
                width: 20px;
            }
            QComboBox {
                background-color: white;
                border: 1px solid #cccccc;
                color: #333333;
                padding: 5px;
                border-radius: 4px;
            }
            QComboBox::drop-down {
                border: none;
            }
            QComboBox QAbstractItemView {
                background-color: white;
                color: #333333;
                border: 1px solid #cccccc;
            }
            QLabel {
                color: #555555;
            }
            QLabel#titleLabel {
                font-size: 24px;
                font-weight: bold;
                color: #4caf50;
                margin: 10px;
            }
            QListWidget {
                background-color: white;
                border: 1px solid #cccccc;
                color: #333333;
            }
            QCheckBox:disabled {
                color: #aaaaaa;
            }
            QFrame#sidebar {
                background-color: #e8e8e8;
                border-right: 1px solid #cccccc;
            }
            QPushButton#navButton {
                background-color: #ffffff;
                border: 1px solid #cccccc;
                color: #555555;
                padding: 12px 16px;
                font-size: 14px;
                border-radius: 5px;
                text-align: left;
                margin: 4px 0px;
            }
            QPushButton#navButton:hover {
                background-color: #f0f0f0;
                color: #333333;
            }
            QPushButton#navButton:checked {
                background-color: #4a86e8;
                color: #ffffff;
                font-weight: bold;
            }

            QPushButton#cancelButton {
                background-color: #e74c3c;
                font-weight: bold;
                padding: 12px;
                font-size: 16px;
            }
            QPushButton#cancelButton:hover {
                background-color: #c0392b;
            }
        """)

    def change_theme(self, theme):
        """更改主题"""
        if theme != self.current_theme:
            self.apply_theme(theme)
            # 保存主题设置
            self.settings.setValue("theme", theme)
            self.statusBar().showMessage(f"已切换至{theme}主题，设置已保存")

    def save_settings(self):
        """保存所有设置"""
        # 保存主题
        self.settings.setValue("theme", self.current_theme)
        # 保存编码设置
        self.settings.setValue("encoding_index", self.encoding_combo.currentIndex())
        # 保存分词设置
        if self.jieba_modern_cb.isChecked():
            segment_mode = 'jieba_modern'
        elif self.jieba_ancient_cb.isChecked():
            segment_mode = 'jieba_ancient'
        else:
            segment_mode = 'none'
        self.settings.setValue("segment_mode", segment_mode)
        # 保存自定义转换表设置
        self.settings.setValue("custom_dict_enabled", self.custom_dict_cb.isChecked())
        self.settings.setValue("custom_dict_entries", self.custom_dict_edit.toPlainText())
        self.settings.setValue("custom_dict_apply_type", self.custom_dict_type_combo.currentData())

        # 保存PDF校对模式设置
        self.settings.setValue("pdf_highlight", self.highlight_pdf_cb.isChecked())

    def create_conversion_tab(self):
        """创建转换选项卡"""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(20)  # 增加垂直间距
        layout.setContentsMargins(15, 15, 15, 15)  # 增加边距

        # 文件选择区域
        file_group = QGroupBox("文件选择")
        file_layout = QVBoxLayout(file_group)
        file_layout.setSpacing(12)  # 增加内部控件间距
        file_layout.setContentsMargins(15, 20, 15, 15)  # 增加内边距，顶部更多

        # 输入路径
        input_layout = QHBoxLayout()
        input_layout.setSpacing(10)
        self.input_edit = QLineEdit()
        self.input_edit.setPlaceholderText("请选择要转换的文件或文件夹...")
        input_browse_btn = QPushButton("浏览")
        input_browse_btn.setObjectName("browseButton")
        input_browse_btn.clicked.connect(self.browse_input)
        self.input_edit.textEdited.connect(self.on_input_edited)
        input_layout.addWidget(QLabel("输入路径:"))
        input_layout.addWidget(self.input_edit)
        input_layout.addWidget(input_browse_btn)
        file_layout.addLayout(input_layout)

        # 输出路径
        output_layout = QHBoxLayout()
        output_layout.setSpacing(10)
        self.output_edit = QLineEdit()
        self.output_edit.setPlaceholderText("请选择输出文件夹...")
        output_browse_btn = QPushButton("浏览")
        output_browse_btn.setObjectName("browseButton")
        output_browse_btn.clicked.connect(self.browse_output)
        output_layout.addWidget(QLabel("输出路径:"))
        output_layout.addWidget(self.output_edit)
        output_layout.addWidget(output_browse_btn)
        file_layout.addLayout(output_layout)

        layout.addWidget(file_group)

        # 转换选项区域
        options_group = QGroupBox("转换选项")
        options_layout = QHBoxLayout(options_group)
        options_layout.setSpacing(30)  # 增加选项之间的水平间距
        options_layout.setContentsMargins(15, 20, 15, 15)  # 增加内边距

        # 转换类型选择
        type_layout = QVBoxLayout()
        type_layout.setSpacing(8)
        type_layout.addWidget(QLabel("转换类型:"))
        self.type_combo = QComboBox()
        self.type_combo.addItem("简体到繁体（OpenCC标准）")
        self.type_combo.addItem("繁体（OpenCC标准）到简体")
        self.type_combo.addItem("简体到繁体（《通用规范汉字表》标准）")
        self.type_combo.addItem("繁体（《通用规范汉字表》标准）到简体")
        self.type_combo.addItem("繁体（任意标准）到繁体（《通用规范汉字表》标准）")
        self.type_combo.addItem("简体到台湾正体")
        self.type_combo.addItem("台湾正体到简体")
        self.type_combo.addItem("简体到香港繁体")
        self.type_combo.addItem("香港繁体到简体")
        self.type_combo.addItem("简体到繁体（台湾正体标准）并转换为台湾常用词汇")
        self.type_combo.addItem("繁体（台湾正体标准）到简体并转换为大陆常用词汇")
        self.type_combo.addItem("繁体（OpenCC标准）到台湾正体")
        self.type_combo.addItem("台湾正体到繁体（OpenCC标准）")
        self.type_combo.addItem("香港繁体到繁体（OpenCC标准）")
        self.type_combo.addItem("繁体（OpenCC标准）到香港繁体")
        self.type_combo.addItem("简体到香港繁体（香港常用词汇）")
        self.type_combo.addItem("香港繁体到简体（大陆常用词汇）")
        self.type_combo.addItem("繁体（OpenCC标准，旧字体）到日文新字体")
        self.type_combo.addItem("日文新字体到繁体（OpenCC标准，旧字体）")
        self.type_combo.addItem("繁体汉字到小篆（Unicode 18.0 篆书区块）")
        self.type_combo.addItem("简体到小篆（Unicode 18.0 篆书区块）")
        self.type_combo.addItem("小篆（Unicode 18.0 篆书区块）到繁体汉字")
        type_layout.addWidget(self.type_combo)
        options_layout.addLayout(type_layout)

        # 高级选项
        advanced_layout = QVBoxLayout()
        advanced_layout.setSpacing(10)

        # 保留格式选项 - 设置为不可用
        self.preserve_format_cb = QCheckBox("尽量保留Word文档的原有格式")
        self.preserve_format_cb.setChecked(True)
        self.preserve_format_cb.setEnabled(True)  # 设置为可用
        self.preserve_format_cb.setToolTip("是否保留Word文档的原有格式")

        # 转换脚注选项 - 设置为可用
        self.convert_footnotes_cb = QCheckBox("转换Word文档里的脚注和尾注")
        self.convert_footnotes_cb.setChecked(True)
        self.convert_footnotes_cb.setEnabled(True)  # 设置为可用
        self.convert_footnotes_cb.setToolTip("是否转换文档中的脚注和尾注内容")

        advanced_layout.addWidget(self.preserve_format_cb)
        advanced_layout.addWidget(self.convert_footnotes_cb)
        options_layout.addLayout(advanced_layout)

        layout.addWidget(options_group)

        # 控制按钮区域
        control_layout = QHBoxLayout()
        self.start_button = QPushButton("开始转换")
        self.start_button.setObjectName("startButton")
        self.start_button.clicked.connect(self.start_conversion)
        control_layout.addWidget(self.start_button)

        self.cancel_button = QPushButton("取消")
        self.cancel_button.setObjectName("cancelButton")
        self.cancel_button.setEnabled(False)  # 初始状态不可用
        self.cancel_button.clicked.connect(self.cancel_conversion)
        control_layout.addWidget(self.cancel_button)

        control_layout.addStretch()
        layout.addLayout(control_layout)

        # 进度区域
        progress_group = QGroupBox("进度")
        progress_layout = QVBoxLayout(progress_group)
        progress_layout.setSpacing(30)
        progress_layout.setContentsMargins(15, 20, 15, 15)

        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        self.progress_label = QLabel("准备就绪")
        self.progress_label.setAlignment(Qt.AlignCenter)
        self.log_text = QTextEdit()
        self.log_text.setMaximumHeight(225)
        self.log_text.setReadOnly(True)

        progress_layout.addWidget(self.progress_bar)
        progress_layout.addWidget(self.progress_label)
        progress_layout.addWidget(self.log_text)

        layout.addWidget(progress_group)

        return tab

    def create_about_tab(self):
        """创建关于选项卡"""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(15, 15, 15, 15)

        # 描述区域
        desc_label = QLabel(f"""
        <h2>简繁通转换大师 V{VERSION}</h2>
        <p>专业的中文繁、简转换工具，助您转换文本文件中的繁、简字形。</p>
        <p>完全免费、开源、无广告，无需注册和登录即可使用。</p>        
        <p><b>主要特性:</b></p>
        <ul>
            <li>拓展OpenCC开源特性，支持陆、台、港三地标准繁体互转</li>
            <li>支持DOCX文档、TXT文本文件、Markdown文件的中文繁、简转换</li>
            <li>支持EPUB电子出版文件的中文简、繁转换</li>
            <li>有限支持PDF文件的中文简、繁转换（扫描件除外）</li>
            <li>支持字幕文件（SRT、ASS、SSA、LRC）的中文繁、简转换</li>
            <li>支持批量处理文件转换</li>
            <li>转换后默认保留DOCX文档格式、排版不变</li>
            <li>支持转换DOCX文档中的脚注和尾注文本</li>
            <li>所有转换均在本地进行，无需联网，保障您的数据安全</li>
        </ul>
        <p><b>请从以下页面获取本工具最新版本：</p>
        <ul>
              <p>主仓库（Github）： https://github.com/TerryTian-tech/oh-my-opencc
              <p>镜像1（Gitee）：https://gitee.com/terrytian-tech/opencc-docx-converter
              <p>镜像2（GitCode）：https://gitcode.com/TerryTian-tech/OpenCC-DocxConverter
        </ul>
        <p><b>本软件遵循Apache-2.0开源协议发布。</p>
        <p>交流繁简转换相关问题可进QQ群：1055649831。</p>
        """)
        desc_label.setWordWrap(True)
        desc_label.setAlignment(Qt.AlignLeft)
        layout.addWidget(desc_label)
        check_update_btn = QPushButton("检查更新")
        check_update_btn.setObjectName("browseButton")
        check_update_btn.clicked.connect(self.check_for_updates)
        layout.addWidget(check_update_btn, alignment=Qt.AlignCenter)

        layout.addStretch()
        return tab

    def create_text_tab(self):
        """创建文字转换选项卡：顶部选转换类型与候选字显示，左侧输入、右侧输出"""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(12)
        layout.setContentsMargins(15, 15, 15, 15)

        # 顶部选项：转换类型 + 是否显示候选字 + 转换按钮
        options_group = QGroupBox("转换选项")
        options_layout = QHBoxLayout(options_group)
        options_layout.setSpacing(15)
        options_layout.setContentsMargins(15, 12, 15, 12)

        options_layout.addWidget(QLabel("转换类型:"))
        self.text_type_combo = QComboBox()
        for display_name in CONVERSION_TYPES:
            self.text_type_combo.addItem(display_name)
        # 小篆转换项提示：显示效果依赖系统安装的 Seal Sans 字体
        seal_tip = ("小篆字符需要系统安装 “Seal Sans” 字体才能正常显示。\n"
                    "该字体为 AGPL 授权，不随本软件分发，需自行安装；未安装时字符可能显示为方框。")
        for i in range(self.text_type_combo.count()):
            if "小篆" in self.text_type_combo.itemText(i):
                self.text_type_combo.setItemData(i, seal_tip, Qt.ToolTipRole)
        self.text_type_combo.currentTextChanged.connect(self.on_text_type_changed)
        options_layout.addWidget(self.text_type_combo, 1)

        self.show_candidates_cb = QCheckBox("显示候选字（红色波浪线标注歧义）")
        self.show_candidates_cb.setChecked(True)
        self.show_candidates_cb.setToolTip(
            "转换结果中的一对多歧义词以红色波浪线标注，\n点击波浪线可查看该词的全部候选转换值。"
        )
        self.show_candidates_cb.stateChanged.connect(self.on_show_candidates_changed)
        options_layout.addWidget(self.show_candidates_cb)

        self.text_convert_btn = QPushButton("转换")
        self.text_convert_btn.setObjectName("startButton")
        self.text_convert_btn.clicked.connect(self.on_text_convert)
        options_layout.addWidget(self.text_convert_btn)

        layout.addWidget(options_group)

        # 中部：左输入、右输出（两侧均为纯文本、自动换行、同一字号，保证显示对齐；
        # 小篆配置时对应侧的字符用系统 Seal Sans 字体渲染，见 _seal_char_format）
        panel_font = QFont(self.font())
        panel_font.setPointSize(11)
        self._panel_font = panel_font  # 面板基准字体（字号来源）

        panels_layout = QHBoxLayout()
        panels_layout.setSpacing(12)

        input_group = QGroupBox("输入文字")
        input_layout = QVBoxLayout(input_group)
        input_layout.setContentsMargins(10, 12, 10, 10)
        self.text_input_edit = QTextEdit()
        self.text_input_edit.setAcceptRichText(False)  # 粘贴/拖入一律按纯文本处理，清除来源格式
        self.text_input_edit.setLineWrapMode(QTextEdit.WidgetWidth)
        self.text_input_edit.setFont(panel_font)
        self.text_input_edit.setPlaceholderText("输入文字，按 Ctrl+Enter 或点击“转换”开始…")
        self.text_input_edit.textChanged.connect(self.on_text_input_changed)
        for seq in (QKeySequence("Ctrl+Return"), QKeySequence("Ctrl+Enter")):
            shortcut = QShortcut(seq, self.text_input_edit)
            shortcut.activated.connect(self.on_text_convert)
        input_layout.addWidget(self.text_input_edit)
        panels_layout.addWidget(input_group, 1)

        output_group = QGroupBox("转换结果")
        output_layout = QVBoxLayout(output_group)
        output_layout.setContentsMargins(10, 12, 10, 10)
        self.text_output_edit = AmbiguityOutputEdit()
        self.text_output_edit.setLineWrapMode(QTextEdit.WidgetWidth)
        self.text_output_edit.setFont(panel_font)
        self.text_output_edit.setPlaceholderText("转换结果显示在这里")
        self.text_output_edit.ambiguity_clicked.connect(self.show_candidate_menu)
        output_layout.addWidget(self.text_output_edit)
        panels_layout.addWidget(output_group, 1)

        layout.addLayout(panels_layout, 1)

        # 底部：操作按钮与状态信息
        bottom_layout = QHBoxLayout()
        bottom_layout.setSpacing(10)

        self.text_copy_btn = QPushButton("复制结果")
        self.text_copy_btn.setObjectName("browseButton")
        self.text_copy_btn.clicked.connect(self.copy_text_result)
        bottom_layout.addWidget(self.text_copy_btn)

        self.text_clear_btn = QPushButton("清空")
        self.text_clear_btn.setObjectName("browseButton")
        self.text_clear_btn.clicked.connect(self.clear_text_tab)
        bottom_layout.addWidget(self.text_clear_btn)

        bottom_layout.addStretch()

        self.text_status_label = QLabel("输入文字后点击“转换”")
        self.text_status_label.setWordWrap(True)
        bottom_layout.addWidget(self.text_status_label, 1)

        layout.addLayout(bottom_layout)

        # 文字转换运行期状态
        self.text_worker = None
        self._text_result = None  # 最近一次转换结果（切换“显示候选字”时用于重绘）
        self._text_defs = []      # 最近一次转换的来源词列表（def 行）
        self._text_config = None  # 最近一次转换使用的 OpenCC 配置
        self._text_seal_format = None  # 最近一次转换结果的小篆字符格式

        # 按初始转换类型套用输入面板的小篆字符格式
        self.apply_text_panel_fonts()

        return tab

    def _seal_panel_for_config(self, config_name):
        """返回配置名对应的小篆显示面板（"input"/"output"）；非小篆配置返回 None。

        config_name 可为带 jieba 分词后缀和 .json 扩展名的完整配置名。
        """
        base = config_name[:-len(".json")] if config_name.endswith(".json") else config_name
        for suffix in ("_jieba_traditional", "_jieba"):
            if base.endswith(suffix):
                base = base[:-len(suffix)]
                break
        return SEAL_CONFIG_PANELS.get(base)

    def _seal_char_format(self, family):
        """构造小篆字符格式：显式指定字体并禁用字体合并。

        Qt 内置的字符覆盖数据尚未收录 Unicode 18.0 篆书区块，即使把面板字体
        设为 Seal Sans，文本分项仍会把小篆字符判定为缺字、交给回退字体而
        显示为方框；必须按字符显式指定字体并配合 NoFontMerging 才能生效。
        """
        font = QFont(family)
        font.setPointSize(self._panel_font.pointSize())
        font.setStyleStrategy(QFont.StyleStrategy.NoFontMerging)
        fmt = QTextCharFormat()
        fmt.setFont(font)
        return fmt

    def apply_text_panel_fonts(self):
        """按当前转换类型刷新面板的小篆显示设置。

        输入面板（seal2t 配置）：当前字符格式设为 Seal Sans，之后粘贴/输入
        的小篆文字即以该字体显示。输出面板的格式在每次转换完成后由
        on_text_convert_done 按字符套用（见 AmbiguityOutputEdit.display_result）。
        """
        seal_family = find_seal_font_family()
        seal_panel = self._seal_panel_for_config(
            CONVERSION_TYPES.get(self.text_type_combo.currentText(), ""))
        if seal_panel == "input" and seal_family:
            self.text_input_edit.setCurrentCharFormat(self._seal_char_format(seal_family))
        else:
            self.text_input_edit.setCurrentCharFormat(QTextCharFormat())

    def on_text_type_changed(self, _text=None):
        """切换转换类型：刷新小篆显示设置；小篆配置在缺少字体时提示手动安装"""
        self.apply_text_panel_fonts()
        config = CONVERSION_TYPES.get(self.text_type_combo.currentText(), "")
        if self._seal_panel_for_config(config) and not find_seal_font_family():
            self.set_text_status(
                "未检测到系统字体 “Seal Sans”：小篆字符可能显示为方框。\n"
                "该字体为 AGPL 授权，不随本软件分发，请自行安装后重启本软件。", error=True)

    def on_text_input_changed(self):
        """输入内容变化后，之前的转换结果与候选信息不再有效"""
        if self._text_result is not None:
            self._text_result = None
            self._text_defs = []
            self.text_output_edit.display_result("", [], False)
            self.set_text_status("输入已修改，请重新转换")

    def on_text_convert(self):
        """执行文字转换（带一对多歧义标注）"""
        if self.text_worker is not None and self.text_worker.isRunning():
            self.set_text_status("正在转换中，请稍候…")
            return

        text = self.text_input_edit.toPlainText()
        if not text.strip():
            self.set_text_status("请先输入要转换的文字", error=True)
            return
        # 统一换行符，保证歧义跨度与 QTextEdit 中的显示位置一一对应
        text = text.replace("\r\n", "\n").replace("\r", "\n")

        # 转换类型：与文件转换一致，应用设置中的分词后缀
        conversion_type = CONVERSION_TYPES[self.text_type_combo.currentText()]
        if self.jieba_modern_cb.isChecked():
            conversion_type += "_jieba"
        elif self.jieba_ancient_cb.isChecked():
            conversion_type += "_jieba_traditional"

        # 自定义转换表：启用且应用于当前转换类型时，生成临时配置
        custom_config_path = None
        if self.custom_dict_cb.isChecked():
            base_type = conversion_type
            for suffix in ("_jieba_traditional", "_jieba"):
                if base_type.endswith(suffix):
                    base_type = base_type[:-len(suffix)]
                    break
            if base_type == self.custom_dict_type_combo.currentData():
                try:
                    custom_entries = parse_custom_entries(self.custom_dict_edit.toPlainText())
                except ValueError as e:
                    QMessageBox.warning(self, "自定义转换表格式错误", str(e))
                    return
                if not custom_entries:
                    QMessageBox.warning(
                        self, "自定义转换表",
                        "未填写任何有效的自定义规则（示例行以 # 开头，需取消注释后生效；\n"
                        "每一行一条：原词→目标词）"
                    )
                    return
                try:
                    custom_config_path = build_custom_config_file(conversion_type, custom_entries)
                except Exception as e:
                    QMessageBox.critical(self, "自定义转换表", f"生成自定义转换配置失败：{e}")
                    return

        config = custom_config_path if custom_config_path else conversion_type + ".json"

        # 候选查询始终使用基础配置：def 来源词若被自定义转换表（单值）命中就不会
        # 成为歧义词，因此基础配置下的候选与完整配置一致，且不受临时配置文件
        # 生命周期影响
        self._text_config = conversion_type + ".json"
        self.text_convert_btn.setEnabled(False)
        self.set_text_status("正在转换…")
        self.text_worker = TextConversionWorker(text, config, custom_config_path)
        self.text_worker.conversion_done.connect(self.on_text_convert_done)
        self.text_worker.conversion_failed.connect(self.on_text_convert_failed)
        self.text_worker.start()

    def on_text_convert_done(self, result):
        """文字转换完成，渲染结果与歧义标注"""
        self.text_convert_btn.setEnabled(True)
        self._text_result = result
        self._text_defs = result["defs"]
        mark = self.show_candidates_cb.isChecked()
        seal_format = None
        if self._seal_panel_for_config(self._text_config) == "output":
            seal_family = find_seal_font_family()
            if seal_family:
                seal_format = self._seal_char_format(seal_family)
        self._text_seal_format = seal_format
        self.text_output_edit.display_result(
            result["output"], result["spans"], mark, seal_format)
        message = f"转换完成：{len(result['output'])} 个字符"
        if mark and result["spans"]:
            message += f"，{len(result['spans'])} 处一对多歧义（点击红色波浪线查看候选）"
        if self._seal_panel_for_config(self._text_config) and not find_seal_font_family():
            message += "；未安装 “Seal Sans” 字体，小篆字符可能显示为方框"
        self.set_text_status(message)

    def on_text_convert_failed(self, message):
        """文字转换失败"""
        self.text_convert_btn.setEnabled(True)
        if "Segmentation plugin" in message and "not found" in message:
            message = ("分词插件未安装，无法使用结巴分词转换（详见 README 安装说明）：\n" + message)
        self.set_text_status(message, error=True)

    def on_show_candidates_changed(self, state):
        """切换“显示候选字”后按最近一次转换结果重绘"""
        if self._text_result is not None:
            mark = self.show_candidates_cb.isChecked()
            self.text_output_edit.display_result(
                self._text_result["output"], self._text_result["spans"], mark,
                self._text_seal_format)

    def set_text_status(self, message, error=False):
        """更新文字转换页底部的状态信息"""
        self.text_status_label.setText(message)
        self.text_status_label.setStyleSheet("color: #e74c3c;" if error else "")

    def show_candidate_menu(self, span_idx, global_pos):
        """在歧义跨度处弹出候选字菜单"""
        span = self.text_output_edit.spans()[span_idx]
        if 0 <= span["def_idx"] < len(self._text_defs):
            def_word = self._text_defs[span["def_idx"]]
        else:
            def_word = "?"

        menu = QMenu(self)

        # 小篆配置下来源词/候选值可能位于篆书区块，QMenu 默认字体会把这些
        # 字符渲染为方框（Qt 尚未收录 Unicode 18.0 区块的覆盖数据）。仅当
        # 菜单项文本被 Seal Sans 全部收录时才改用该字体并禁用字体合并；
        # “（当前）”标记相应改为菜单勾选（样式绘制，不混入文本），来源词
        # 为小篆时表头拆成两行，避免“原文：”等缺字字符与篆书混排。
        seal_family = None
        if self._seal_panel_for_config(self._text_config) is not None:
            seal_family = find_seal_font_family()

        def seal_font_for(text):
            if not seal_family:
                return None
            if not text or not all(seal_covered_mask(text, seal_family)):
                return None
            font = QFont(menu.font())
            font.setFamily(seal_family)
            font.setStyleStrategy(QFont.StyleStrategy.NoFontMerging)
            return font

        def_word_font = seal_font_for(def_word)
        if def_word_font is not None:
            header = QAction("原文：", menu)
            header.setEnabled(False)
            menu.addAction(header)
            word_action = QAction(def_word, menu)
            word_action.setFont(def_word_font)
            word_action.setEnabled(False)
            menu.addAction(word_action)
        else:
            header = QAction(f"原文：{def_word}", menu)
            header.setEnabled(False)
            menu.addAction(header)

        candidates = query_candidates(self._text_config, def_word)
        if candidates:
            current = self.text_output_edit.span_text(span_idx)
            for candidate in candidates:
                action = menu.addAction(candidate)
                font = seal_font_for(candidate)
                if font is not None:
                    action.setFont(font)
                action.setCheckable(True)
                action.setChecked(candidate == current)
                action.triggered.connect(
                    lambda checked, c=candidate, i=span_idx:
                        self.text_output_edit.apply_candidate(i, c))
        else:
            hint = menu.addAction("当前 OpenCC 暂不支持词级候选查询")
            hint.setEnabled(False)

        menu.exec(global_pos)

    def copy_text_result(self):
        """复制转换结果到剪贴板"""
        text = self.text_output_edit.toPlainText()
        if not text:
            self.set_text_status("暂无转换结果可复制", error=True)
            return
        QApplication.clipboard().setText(text)
        self.set_text_status("转换结果已复制到剪贴板")

    def clear_text_tab(self):
        """清空输入与输出"""
        self.text_input_edit.clear()
        self._text_result = None
        self._text_defs = []
        self.text_output_edit.display_result("", [], False)
        self.set_text_status("已清空")

    # 检查更新方法
    def check_for_updates(self):
        """检查是否有新版本"""
        self.statusBar().showMessage("正在检查更新...")
        self.update_checker = UpdateChecker()
        self.update_checker.update_checked.connect(self.on_update_checked)
        self.update_checker.start()

    # 处理更新检查结果
    def on_update_checked(self, has_new, latest_version, url):
        if has_new:
            reply = QMessageBox.question(
                self,
                "发现新版本",
                f"当前版本：{VERSION}\n最新版本：{latest_version}\n\n是否前往下载页面？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes
            )
            if reply == QMessageBox.Yes:
                import webbrowser
                webbrowser.open(url)
        elif latest_version == '' and '失败' in url:
            # 错误情况
            QMessageBox.warning(self, "检查更新失败", url)
        else:
            QMessageBox.information(self, "检查更新", f"当前已是最新版本{VERSION}。")
        self.statusBar().showMessage("就绪")

    def browse_input(self):
        """浏览输入路径"""
        # 使用标准QMessageBox，但修改按钮文本
        msg_box = QMessageBox(
            QMessageBox.Question,
            "选择类型",
            "批量转换同一目录下所有文档请选择文件夹，转换单个或多个文档请选择文件。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel,
            self
        )

        # 修改按钮文本
        yes_button = msg_box.button(QMessageBox.StandardButton.Yes)
        no_button = msg_box.button(QMessageBox.StandardButton.No)
        cancel_button = msg_box.button(QMessageBox.StandardButton.Cancel)
        yes_button.setText("选择文件夹")
        no_button.setText("选择文件")
        cancel_button.setText("取消")

        # 显示对话框并等待用户选择
        choice = msg_box.exec()

        if choice == QMessageBox.StandardButton.Yes:  # 文件夹
            path = QFileDialog.getExistingDirectory(self, "选择输入文件夹")
            if path:
                self.input_edit.setText(path)
                self.selected_files = []  # 清空多选文件列表
        elif choice == QMessageBox.StandardButton.No:  # 文件
            paths, _ = QFileDialog.getOpenFileNames(
                self, "选择文件", "",
                "文档文件 (*.docx *.txt *.md *.srt *.ass *.ssa *.lrc *.epub *.pdf);;所有文件 (*)"
            )
            if paths:
                self.selected_files = paths
                if len(paths) == 1:
                    self.input_edit.setText(paths[0])
                else:
                    self.input_edit.setText(f"已选择 {len(paths)} 个文件")

    def on_input_edited(self):
        """用户手动编辑输入框时，清空多选文件列表"""
        self.selected_files = []
        # 如果当前显示的是多选占位文本，清空输入框以避免状态不同步
        text = self.input_edit.text()
        if text.startswith("已选择 ") and text.endswith(" 个文件"):
            self.input_edit.clear()

    def browse_output(self):
        """浏览输出路径"""
        path = QFileDialog.getExistingDirectory(self, "选择输出文件夹")
        if path:
            self.output_edit.setText(path)

    def start_conversion(self):
        """开始转换"""
        input_path = self.input_edit.text()
        output_path = self.output_edit.text()

        if not input_path or not output_path:
            QMessageBox.warning(self, "警告", "请输入完整的路径信息")
            return

        # 验证输入路径
        if self.selected_files:
            # 多文件模式：验证每个文件是否存在
            missing_files = [f for f in self.selected_files if not os.path.exists(f)]
            if missing_files:
                QMessageBox.critical(self, "错误", f"以下文件不存在:\n" + "\n".join(missing_files))
                return
        else:
            if not os.path.exists(input_path):
                QMessageBox.critical(self, "错误", "输入路径不存在")
                return

        # 获取转换类型
        conversion_type = CONVERSION_TYPES[self.type_combo.currentText()]

        # 获取转换选项的实际值
        preserve_format = self.preserve_format_cb.isChecked()
        convert_footnotes = self.convert_footnotes_cb.isChecked()
        highlight_pdf = self.highlight_pdf_cb.isChecked()

        # 获取强制编码选项
        force_encoding = self.encoding_combo.currentData()

        # 获取分词模式
        if self.jieba_modern_cb.isChecked():
            segment_mode = 'jieba_modern'
        elif self.jieba_ancient_cb.isChecked():
            segment_mode = 'jieba_ancient'
        else:
            segment_mode = None

        # 根据分词模式自动调整OpenCC配置名称
        if segment_mode == 'jieba_modern':
            conversion_type += '_jieba'
        elif segment_mode == 'jieba_ancient':
            conversion_type += '_jieba_traditional'

        # 生成自定义转换表配置（启用且与指定应用类型匹配时）
        custom_config_path = None
        custom_entries_count = 0
        custom_dict_display = "未启用"
        if self.custom_dict_cb.isChecked():
            # 当前转换类型的基础名称（去掉分词后缀，如 s2t_jieba -> s2t）
            base_type = conversion_type
            for suffix in ('_jieba_traditional', '_jieba'):
                if base_type.endswith(suffix):
                    base_type = base_type[:-len(suffix)]
                    break

            apply_type = self.custom_dict_type_combo.currentData()
            if base_type != apply_type:
                custom_dict_display = f"未应用（仅应用于“{self.custom_dict_type_combo.currentText()}”）"
            else:
                try:
                    custom_entries = parse_custom_entries(self.custom_dict_edit.toPlainText())
                except ValueError as e:
                    QMessageBox.warning(self, "自定义转换表格式错误", str(e))
                    return
                if not custom_entries:
                    QMessageBox.warning(
                        self, "自定义转换表",
                        "未填写任何有效的自定义规则（示例行以 # 开头，需取消注释后生效；\n"
                        "每一行一条：原词→目标词）"
                    )
                    return
                try:
                    custom_config_path = build_custom_config_file(conversion_type, custom_entries)
                except Exception as e:
                    QMessageBox.critical(self, "自定义转换表", f"生成自定义转换配置失败：{e}")
                    return
                custom_entries_count = len(custom_entries)
                custom_dict_display = f"启用（{custom_entries_count}条规则）"

        # 如果已有转换线程在运行，先取消并等待其结束
        if hasattr(self, 'worker') and self.worker.isRunning():
            self.worker.cancel()
            self.worker.wait()

        # 启动转换线程
        self.start_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.progress_bar.setValue(0)
        self.log_text.clear()

        # 在日志中显示当前设置
        segment_mode_display = {
            None: '不分词',
            'jieba_modern': '结巴分词（现代汉语）',
            'jieba_ancient': '结巴分词（古汉语）'
        }
        self.append_log(f"转换设置：保留格式={preserve_format}，转换脚注={convert_footnotes}，强制编码={force_encoding or '自动'}，分词模式={segment_mode_display.get(segment_mode, '不分词')}，自定义转换表={custom_dict_display}，PDF修改字高亮={'开' if highlight_pdf else '关'}")

        try:
            self.worker = ConversionWorker(
                input_path,
                output_path,
                conversion_type,
                preserve_format,  # 使用复选框的实际值
                convert_footnotes,  # 使用复选框的实际值
                force_encoding,
                segment_mode,
                input_paths=self.selected_files if self.selected_files else None,
                custom_config_path=custom_config_path,
                highlight_pdf=highlight_pdf
            )
        except Exception as e:
            # 如果线程启动失败，清理已生成的临时配置文件并提示
            if custom_config_path and os.path.exists(custom_config_path):
                try:
                    os.remove(custom_config_path)
                except OSError:
                    pass
            QMessageBox.critical(self, "错误", f"启动转换线程失败：{e}")
            return
        self.worker.progress_updated.connect(self.update_progress)
        self.worker.conversion_finished.connect(self.conversion_finished)
        self.worker.log_message.connect(self.append_log)
        self.worker.start()

    def update_progress(self, value, message):
        """更新进度"""
        self.progress_bar.setValue(value)
        self.progress_label.setText(message)
        self.append_log(f"[{value}%] {message}")

    def append_log(self, message):
        """添加日志消息"""
        self.log_text.append(message)
        self.log_text.verticalScrollBar().setValue(
            self.log_text.verticalScrollBar().maximum()
        )

    def cancel_conversion(self):
        """取消转换"""
        if hasattr(self, 'worker') and self.worker.isRunning():
            self.worker.cancel()  # 调用自定义的取消方法
            self.append_log("正在取消转换...")
            self.statusBar().showMessage("正在取消转换...")

    def conversion_finished(self, success, message, success_count, total_files):
        """转换完成"""
        self.start_button.setEnabled(True)
        self.cancel_button.setEnabled(False)  # 转换完成后禁用取消按钮

        if success:
            QMessageBox.information(self, "成功", message)
            self.statusBar().showMessage("转换完成")
        else:
            if "取消" in message or "已取消" in message:
                QMessageBox.information(self, "已取消", "转换已被用户取消")
                self.statusBar().showMessage("转换已取消")
            elif "部分" in message:
                QMessageBox.warning(self, "部分失败", f"成功转换 {success_count}/{total_files} 个文件，部分文件转换失败")
                self.statusBar().showMessage("部分转换失败")
            else:
                QMessageBox.critical(self, "错误", message)
                self.statusBar().showMessage("转换失败")

    def closeEvent(self, event):
        """窗口关闭事件，保存设置"""
        # 如果转换正在进行，先取消
        if hasattr(self, 'worker') and self.worker.isRunning():
            self.worker.cancel()
            self.worker.wait()

        # 如果文字转换正在进行，先终止
        if getattr(self, 'text_worker', None) is not None and self.text_worker.isRunning():
            self.text_worker.cancel()
            self.text_worker.wait()

        # 保存当前设置
        self.save_settings()
        event.accept()


def main():
    app = QApplication(sys.argv)
    app.setStyle(QStyleFactory.create("Fusion"))  # 使用现代样式

    # 设置应用程序图标（会显示在任务栏）
    # 注意：Windows上可能还需要单独的.ico文件才能正确显示任务栏图标
    app_icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logo.ico")
    if os.path.exists(app_icon_path):
        app.setWindowIcon(QIcon(app_icon_path))
    else:
        # 如果没有找到图标文件，可以尝试png格式
        app_icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logo.png")
        if os.path.exists(app_icon_path):
            app.setWindowIcon(QIcon(app_icon_path))

    window = ModernUI()
    window.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
