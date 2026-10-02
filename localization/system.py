"""本地化系统 - 含5个bug"""
from dataclasses import dataclass, field

@dataclass
class LanguagePack:
    lang: str
    version: int
    strings: dict

class LocalizationSystem:
    def __init__(self):
        self.languages: dict[str, LanguagePack] = {}
        self.current: str = "en"
        self.default: str = "en"

    def load_language(self, lang: str, strings: dict, version: int = 1) -> bool:
        # bug1: 不做回退，不检查版本
        self.languages[lang] = LanguagePack(lang, version, strings)
        return True

    def format_text(self, key: str, **kwargs) -> str:
        # bug2: 不支持复数和性别
        pack = self.languages.get(self.current)
        if pack and key in pack.strings:
            return pack.strings[key].format(**kwargs)
        return ""

    def wrap_text(self, text: str, max_width: int) -> list:
        # bug3: 按字符数换行，不支持RTL
        return [text[i:i+max_width] for i in range(0, len(text), max_width)]

    def get_font(self, lang: str, char: str) -> str:
        # bug4: 不做字体回退
        return "default_font"

    def switch_language(self, lang: str) -> bool:
        # bug5: 不刷新文本，不保存设置
        if lang in self.languages:
            self.current = lang
        return True
