"""Read installed macOS font names through CoreText, without exporting font files."""

import ctypes
import sys
from typing import Any


class FontCatalogError(RuntimeError):
    """The operating system could not provide a bounded font catalog."""


def installed_fonts() -> dict[str, Any]:
    if sys.platform != "darwin":
        return {"supported": False, "fonts": []}
    try:
        return {"supported": True, "fonts": _mac_fonts()}
    except (OSError, ValueError, AttributeError) as exc:
        raise FontCatalogError("CoreText font enumeration failed") from exc


def _mac_fonts() -> list[dict[str, str]]:
    cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
    ct = ctypes.CDLL("/System/Library/Frameworks/CoreText.framework/CoreText")
    pointer = ctypes.c_void_p
    index = ctypes.c_long

    def bind(library: Any, name: str, arguments: list[Any], result: Any) -> Any:
        function = getattr(library, name)
        function.argtypes = arguments
        function.restype = result
        return function

    release = bind(cf, "CFRelease", [pointer], None)
    count = bind(cf, "CFArrayGetCount", [pointer], index)
    at = bind(cf, "CFArrayGetValueAtIndex", [pointer, index], pointer)
    length = bind(cf, "CFStringGetLength", [pointer], index)
    get_type = bind(cf, "CFGetTypeID", [pointer], ctypes.c_ulong)
    string_type = bind(cf, "CFStringGetTypeID", [], ctypes.c_ulong)()
    string = bind(
        cf, "CFStringGetCString", [pointer, ctypes.c_char_p, index, ctypes.c_uint32], ctypes.c_bool
    )
    collection = bind(ct, "CTFontCollectionCreateFromAvailableFonts", [pointer], pointer)(None)
    descriptors = None
    if not collection:
        raise FontCatalogError("CoreText did not return a collection")
    try:
        descriptors = bind(ct, "CTFontCollectionCreateMatchingFontDescriptors", [pointer], pointer)(
            collection
        )
        if not descriptors or not 0 < count(descriptors) <= 16384:
            raise FontCatalogError("CoreText returned an empty or oversized catalog")
        attribute = bind(ct, "CTFontDescriptorCopyAttribute", [pointer, pointer], pointer)
        names = {
            "family": "kCTFontFamilyNameAttribute",
            "postscriptName": "kCTFontNameAttribute",
            "style": "kCTFontStyleNameAttribute",
        }
        keys = {name: pointer.in_dll(ct, symbol).value for name, symbol in names.items()}
        records = []
        for position in range(count(descriptors)):
            descriptor = at(descriptors, position)
            record = {}
            for name, key in keys.items():
                value = attribute(descriptor, key)
                if not value:
                    if name == "style":
                        record[name] = "Regular"
                        continue
                    raise FontCatalogError("CoreText returned a missing font name")
                try:
                    if get_type(value) != string_type or not 0 < length(value) <= 256:
                        raise FontCatalogError("CoreText returned an invalid font name")
                    buffer = ctypes.create_string_buffer(length(value) * 4 + 1)
                    if not string(value, buffer, len(buffer), 0x08000100):
                        raise FontCatalogError("CoreText could not encode a font name")
                    text = buffer.value.decode("utf-8")
                    if any(ord(char) < 32 or ord(char) == 127 for char in text):
                        raise FontCatalogError(
                            "CoreText returned control characters in a font name"
                        )
                    record[name] = text
                finally:
                    release(value)
            records.append(record)
        return records
    finally:
        if descriptors:
            release(descriptors)
        release(collection)
