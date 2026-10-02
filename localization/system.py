"""本地化系统

- 语言包缺失 key 回退到默认语言，并校验语言包版本
- 文本替换支持复数形式和性别变体
- 换行按单词边界，支持 CJK 逐字换行与 RTL 语言检测
- 字体按字符覆盖回退，带加载缓存
- 切换语言时刷新文本、重新布局并保存设置
"""
import json
import os
import tempfile
import unicodedata
from dataclasses import dataclass, field


@dataclass
class LanguagePack:
    lang: str
    version: int
    strings: dict


# 各语言的优先字体
LANG_FONT = {
    "en": "en_font",
    "ja": "jp_font",
    "jp": "jp_font",
    "zh": "zh_font",
    "ko": "ko_font",
    "ar": "ar_font",
    "he": "he_font",
}

# 各字体支持的 Unicode 区间（含端点）
FONT_COVERAGE = {
    "en_font": [(0x0000, 0x024F), (0x2000, 0x206F)],
    "jp_font": [(0x3040, 0x30FF), (0x3400, 0x4DBF), (0x4E00, 0x9FFF), (0xFF00, 0xFFEF)],
    "zh_font": [(0x3400, 0x4DBF), (0x4E00, 0x9FFF), (0xFF00, 0xFFEF)],
    "ko_font": [(0xAC00, 0xD7AF), (0x1100, 0x11FF)],
    "ar_font": [(0x0600, 0x06FF), (0x0750, 0x077F), (0xFB50, 0xFDFF), (0xFE70, 0xFEFF)],
    "he_font": [(0x0590, 0x05FF), (0xFB1D, 0xFB4F)],
    "default_font": [(0x0000, 0x10FFFF)],
}

# 从右到左书写的语言
RTL_LANGS = {"ar", "he", "fa", "ur"}

# 复数形式的选择优先级
_PLURAL_ORDER = ("zero", "one", "two", "few", "many", "other", "neutral")

_GENDER_ALIASES = {
    "m": "male", "masculine": "male",
    "f": "female", "feminine": "female",
    "n": "neutral", "neuter": "neutral",
}


def _is_cjk(char: str) -> bool:
    code = ord(char)
    return (
        0x4E00 <= code <= 0x9FFF
        or 0x3400 <= code <= 0x4DBF
        or 0x3040 <= code <= 0x30FF
        or 0xAC00 <= code <= 0xD7AF
        or 0xFF00 <= code <= 0xFFEF
    )


def _is_rtl_char(char: str) -> bool:
    code = ord(char)
    return (
        0x0590 <= code <= 0x05FF
        or 0x0600 <= code <= 0x06FF
        or 0x0750 <= code <= 0x077F
        or 0xFB1D <= code <= 0xFB4F
        or 0xFB50 <= code <= 0xFDFF
        or 0xFE70 <= code <= 0xFEFF
    )


class LocalizationSystem:
    REQUIRED_VERSION = 1

    def __init__(self, settings_path: str = None):
        self.languages: dict[str, LanguagePack] = {}
        self.current: str = "en"
        self.default: str = "en"
        self.required_version: int = self.REQUIRED_VERSION
        self.settings_path = settings_path or os.path.join(
            tempfile.gettempdir(), "localization_settings.json"
        )
        self._listeners: list = []
        self._relayout_listeners: list = []
        self._font_cache: dict = {}
        self._font_load_cache: dict = {}

    # ---------- 语言包加载 ----------

    def load_language(self, lang: str, strings: dict, version: int = 1) -> bool:
        """加载语言包；版本与代码要求不匹配时拒绝加载并返回 False。"""
        if version != self.required_version:
            return False
        self.languages[lang] = LanguagePack(lang, version, dict(strings))
        return True

    def _get_pack(self, lang: str):
        pack = self.languages.get(lang)
        if pack is None:
            return None
        version = getattr(pack, "version", self.required_version)
        if version != self.required_version:
            return None
        return pack

    def _lookup(self, key: str):
        """按 当前语言 -> 默认语言 的顺序查找 key，找不到返回 None。"""
        for lang in (self.current, self.default):
            pack = self._get_pack(lang)
            if pack is not None and key in pack.strings:
                return pack.strings[key], lang
        return None, None

    # ---------- 文本格式化 ----------

    def _plural_category(self, count, lang: str) -> str:
        """CLDR 精简版复数规则，返回 zero/one/two/few/many/other。"""
        n = abs(count)
        base = lang.replace("_", "-").split("-")[0]
        if base in ("ja", "zh", "ko", "th", "vi"):
            return "other"
        if base in ("pl",):
            if n == 1:
                return "one"
            if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
                return "few"
            return "many"
        if base in ("ru", "uk", "be"):
            if n % 10 == 1 and n % 100 != 11:
                return "one"
            if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
                return "few"
            return "many"
        if base == "ar":
            if n == 0:
                return "zero"
            if n == 1:
                return "one"
            if n == 2:
                return "two"
            if 3 <= n % 100 <= 10:
                return "few"
            if 11 <= n % 100 <= 99:
                return "many"
            return "other"
        if base == "fr":
            return "one" if n in (0, 1) else "other"
        return "one" if n == 1 else "other"

    def _select_form(self, template, count=None, gender=None, lang: str = "en"):
        """从 dict 形式的模板中按复数/性别挑选具体字符串。"""
        while isinstance(template, dict):
            chosen = None
            if count is not None:
                category = self._plural_category(count, lang)
                if category in template:
                    chosen = template[category]
                else:
                    for name in _PLURAL_ORDER:
                        if name in template:
                            chosen = template[name]
                            break
            if chosen is None and gender is not None:
                g = _GENDER_ALIASES.get(str(gender).lower(), str(gender).lower())
                chosen = template.get(g) or template.get("neutral") or template.get("other")
            if chosen is None:
                for name in _PLURAL_ORDER:
                    if name in template:
                        chosen = template[name]
                        break
            if chosen is None:
                chosen = next(iter(template.values()))
            template = chosen
        return template

    def _auto_pluralize(self, text: str, count, lang: str) -> str:
        """没有显式复数模板时，对英文做简单的名词复数化（item -> items）。"""
        if count is None or count == 1 or not lang.startswith("en"):
            return text
        words = text.split(" ")
        for i in range(len(words) - 1, -1, -1):
            word = words[i]
            if word and word[0].isalpha() and word[-1].isalpha() and not word.endswith("s"):
                if word.endswith("y") and len(word) > 1 and word[-2] not in "aeiou":
                    words[i] = word[:-1] + "ies"
                elif word.endswith(("s", "x", "z", "ch", "sh")):
                    words[i] = word + "es"
                else:
                    words[i] = word + "s"
                break
        return " ".join(words)

    def format_text(self, key: str, count=None, gender=None, **kwargs) -> str:
        template, lang = self._lookup(key)
        if template is None:
            # 当前语言和默认语言都没有该 key，回退为显示 key 本身而不是空白
            return key
        template = self._select_form(template, count=count, gender=gender, lang=lang or "en")
        if count is not None:
            kwargs.setdefault("count", count)
            template = self._auto_pluralize(template, count, lang or "en")
        try:
            return template.format(**kwargs)
        except (KeyError, IndexError, ValueError):
            return template

    # ---------- 文本换行 ----------

    def is_rtl(self, lang: str = None, text: str = None) -> bool:
        if lang is not None:
            return lang in RTL_LANGS
        if text:
            return any(_is_rtl_char(c) for c in text)
        return self.current in RTL_LANGS

    def get_direction(self, lang: str = None) -> str:
        return "rtl" if self.is_rtl(lang or self.current) else "ltr"

    def wrap_text(self, text: str, max_width: int, lang: str = None) -> list:
        """按单词边界换行；CJK 文本逐字换行；RTL 语言保持逻辑顺序由渲染层处理 BiDi。"""
        if max_width <= 0:
            return [text] if text else []
        if not text:
            return []
        # CJK 文本没有空格，逐字符换行
        if " " not in text and any(_is_cjk(c) for c in text):
            return [text[i:i + max_width] for i in range(0, len(text), max_width)]

        lines = []
        current = ""
        for word in text.split(" "):
            # 超长单词按宽度硬切，避免无限长行
            while len(word) > max_width:
                if current:
                    lines.append(current)
                    current = ""
                lines.append(word[:max_width])
                word = word[max_width:]
            candidate = word if not current else current + " " + word
            if len(candidate) <= max_width:
                current = candidate
            else:
                if current:
                    lines.append(current)
                current = word
        if current:
            lines.append(current)
        return lines

    # ---------- 字体 ----------

    def _font_supports(self, font: str, char: str) -> bool:
        code = ord(char)
        return any(lo <= code <= hi for lo, hi in FONT_COVERAGE.get(font, []))

    def _load_font(self, font: str) -> str:
        """加载字体，带缓存，避免每次渲染重复加载。"""
        if font not in self._font_load_cache:
            self._font_load_cache[font] = font
        return self._font_load_cache[font]

    def get_font(self, lang: str, char: str) -> str:
        """为字符选择支持它的字体：语言优先字体 -> 其他匹配字体 -> 默认字体。"""
        cache_key = (lang, char)
        if cache_key in self._font_cache:
            return self._font_cache[cache_key]

        candidates = []
        preferred = LANG_FONT.get(lang)
        if preferred:
            candidates.append(preferred)
        candidates.extend(f for f in FONT_COVERAGE if f not in candidates and f != "default_font")
        candidates.append("default_font")

        chosen = "default_font"
        for font in candidates:
            if self._font_supports(font, char):
                chosen = font
                break
        result = self._load_font(chosen)
        self._font_cache[cache_key] = result
        return result

    # ---------- 语言切换 ----------

    def add_listener(self, callback) -> None:
        """注册文本刷新回调，切换语言时调用。"""
        self._listeners.append(callback)

    def add_relayout_listener(self, callback) -> None:
        """注册重新布局回调，切换语言时调用。"""
        self._relayout_listeners.append(callback)

    def _save_settings(self) -> None:
        try:
            with open(self.settings_path, "w", encoding="utf-8") as fh:
                json.dump({"language": self.current}, fh)
        except OSError:
            pass

    def load_settings(self) -> None:
        try:
            with open(self.settings_path, "r", encoding="utf-8") as fh:
                lang = json.load(fh).get("language")
        except (OSError, ValueError):
            return
        if lang and lang in self.languages:
            self.current = lang

    def switch_language(self, lang: str) -> bool:
        if lang not in self.languages:
            return False
        self.current = lang
        # 刷新已渲染文本
        for callback in list(self._listeners):
            callback(lang)
        # 通知 UI 重新布局，避免长文本错位
        for callback in list(self._relayout_listeners):
            callback(lang)
        # 保存语言设置，重启后恢复
        self._save_settings()
        return True
