"""本地化系统"""
import json
import os
import re
import unicodedata
from dataclasses import dataclass, field

EXPECTED_PACK_VERSION = 2

# 各文字体系对应的回退字体，按优先级排列
FONT_FALLBACKS = [
    ("CJK", ("cjk_font",)),
    ("KANA", ("cjk_font",)),
    ("HANGUL", ("cjk_font",)),
    ("ARABIC", ("arabic_font",)),
    ("HEBREW", ("hebrew_font",)),
    ("LATIN", ("default_font",)),
]

_RTL_RANGES = (
    "؀-ۿݐ-ݿࢠ-ࣿﭐ-﷿ﹰ-﻿"
    "֐-׿יִ-﬿"
)


@dataclass
class LanguagePack:
    lang: str
    version: int
    strings: dict


def _char_script(char: str) -> str:
    cp = ord(char)
    if 0x4E00 <= cp <= 0x9FFF or 0x3400 <= cp <= 0x4DBF:
        return "CJK"
    if 0x3040 <= cp <= 0x30FF or 0x31F0 <= cp <= 0x31FF:
        return "KANA"
    if 0xAC00 <= cp <= 0xD7AF or 0x1100 <= cp <= 0x11FF:
        return "HANGUL"
    if 0x0600 <= cp <= 0x06FF or 0x0750 <= cp <= 0x077F or 0xFB50 <= cp <= 0xFEFF:
        return "ARABIC"
    if 0x0590 <= cp <= 0x05FF:
        return "HEBREW"
    return "LATIN"


def _is_rtl(text: str) -> bool:
    for ch in text:
        bidi = unicodedata.bidirectional(ch)
        if bidi in ("R", "AL", "AN"):
            return True
        if bidi == "L":
            return False
    return False


def _pluralize(word: str) -> str:
    if re.search(r"(s|x|z|ch|sh)$", word):
        return word + "es"
    if re.search(r"[^aeiou]y$", word):
        return word[:-1] + "ies"
    return word + "s"


class LocalizationSystem:
    def __init__(self, settings_path: str = "settings.json"):
        self.languages: dict[str, LanguagePack] = {}
        self.current: str = "en"
        self.default: str = "en"
        self.expected_version: int = EXPECTED_PACK_VERSION
        self.settings_path = settings_path
        self._font_cache: dict[str, str] = {}
        self._listeners: list = []
        self._load_settings()

    # bug1 修复：加载时校验版本，并用默认语言包补齐缺失的 key
    def load_language(self, lang: str, strings: dict, version: int = 1) -> bool:
        if version != self.expected_version:
            return False
        merged = dict(strings)
        if lang != self.default and self.default in self.languages:
            for key, value in self.languages[self.default].strings.items():
                merged.setdefault(key, value)
        self.languages[lang] = LanguagePack(lang, version, merged)
        return True

    # bug2 修复：支持复数（{count} 后的名词自动复数化 / a|b 选择）和性别变体
    def format_text(self, key: str, **kwargs) -> str:
        gender = kwargs.get("gender")
        template = self._lookup(key, gender)
        if template is None:
            return ""
        template = self._apply_choice(template, kwargs)
        result = template.format(**kwargs)
        count = kwargs.get("count")
        if isinstance(count, int) and count != 1:
            result = self._auto_pluralize(template, result)
        return result

    def _lookup(self, key: str, gender: str | None) -> str | None:
        keys = [f"{key}_{gender}", key] if gender else [key]
        for lang in (self.current, self.default):
            pack = self.languages.get(lang)
            if not pack:
                continue
            for k in keys:
                if k in pack.strings:
                    return pack.strings[k]
        return None

    @staticmethod
    def _apply_choice(template: str, kwargs: dict) -> str:
        # 复数选择：{count} one|many；性别选择：{gender:男|女}
        def repl(match):
            kind, forms = match.group(1), match.group(2).split("|")
            value = kwargs.get(kind)
            if kind == "count":
                return forms[0] if value == 1 else forms[-1]
            if kind == "gender":
                idx = {"male": 0, "female": -1}.get(value, 0)
                return forms[idx]
            return match.group(0)

        return re.sub(r"\{(count|gender):([^}]+)\}", repl, template)

    @staticmethod
    def _auto_pluralize(template: str, result: str) -> str:
        # 模板中 "{count} word" 形式在 count != 1 时把 word 变为复数
        for m in re.finditer(r"\{count\}\s+([A-Za-z]+)", template):
            word = m.group(1)
            result = re.sub(rf"\b{re.escape(word)}\b", _pluralize(word), result, count=1)
        return result

    # bug3 修复：按单词边界换行，RTL 语言按从右到左顺序排列
    def wrap_text(self, text: str, max_width: int) -> list:
        if max_width <= 0:
            return [text]
        words = text.split()
        if not words:
            return []
        lines, current = [], ""
        for word in words:
            candidate = f"{current} {word}".strip()
            if len(candidate) <= max_width:
                current = candidate
            else:
                if current:
                    lines.append(current)
                # 单词本身超长时按字符硬切
                while len(word) > max_width:
                    lines.append(word[:max_width])
                    word = word[max_width:]
                current = word
        if current:
            lines.append(current)
        if _is_rtl(text):
            lines = [" ".join(reversed(line.split())) for line in lines]
        return lines

    # bug4 修复：按字符所属文字体系回退字体，并缓存结果
    def get_font(self, lang: str, char: str) -> str:
        cache_key = f"{lang}:{_char_script(char)}"
        if cache_key in self._font_cache:
            return self._font_cache[cache_key]
        script = _char_script(char)
        font = "default_font"
        for name, fonts in FONT_FALLBACKS:
            if script == name:
                font = fonts[0]
                break
        self._font_cache[cache_key] = font
        return font

    # bug5 修复：切换语言时通知监听者刷新文本与布局，并保存设置
    def switch_language(self, lang: str) -> bool:
        if lang not in self.languages:
            return False
        if lang == self.current:
            return True
        self.current = lang
        self._save_settings()
        for listener in list(self._listeners):
            listener(lang)
        return True

    def add_listener(self, callback) -> None:
        self._listeners.append(callback)

    def remove_listener(self, callback) -> None:
        if callback in self._listeners:
            self._listeners.remove(callback)

    def _save_settings(self) -> None:
        try:
            with open(self.settings_path, "w", encoding="utf-8") as f:
                json.dump({"language": self.current}, f)
        except OSError:
            pass

    def _load_settings(self) -> None:
        try:
            with open(self.settings_path, encoding="utf-8") as f:
                lang = json.load(f).get("language")
            if isinstance(lang, str):
                self.current = lang
        except (OSError, ValueError):
            pass
