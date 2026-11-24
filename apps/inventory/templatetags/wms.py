"""Locale-independent number formatting: money is always shown as $1,234.56."""

from decimal import Decimal, InvalidOperation

from django import template
from django.utils.html import format_html
from django.utils.http import urlencode
from django.utils.safestring import mark_safe
from django.utils.translation import gettext as _
from django.utils.translation import pgettext_lazy

from apps.inventory.models import category_label, unit_short_label
from apps.users.models import role_label as _role_label

register = template.Library()


def _to_decimal(value) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return value if type(value) is Decimal else Decimal(str(value))  # also unwraps lazy objects
    except (InvalidOperation, ValueError, TypeError):
        return None


@register.filter
def usd(value, places: int = 2) -> str:
    number = _to_decimal(value)
    if number is None:
        return "—"
    places = int(places)
    sign = "-" if number < 0 else ""
    return f"{sign}${abs(number):,.{places}f}"


@register.filter
def qty(value) -> str:
    """12.50 -> 12.5, 3.00 -> 3, 1250 -> 1,250."""
    number = _to_decimal(value)
    if number is None:
        return "—"
    if number == number.to_integral_value():
        return f"{number:,.0f}"
    return f"{number.normalize():,f}"


@register.filter
def unit_label(value: str) -> str:
    return unit_short_label(value)


@register.filter
def absval(value):
    number = _to_decimal(value)
    return abs(number) if number is not None else value


# Stand-alone month names (nominative), in the case used mid-sentence: "noyabr", "ноябрь", "November".
MONTHS = (
    pgettext_lazy("month", "yanvar"), pgettext_lazy("month", "fevral"), pgettext_lazy("month", "mart"),
    pgettext_lazy("month", "aprel"), pgettext_lazy("month", "may"), pgettext_lazy("month", "iyun"),
    pgettext_lazy("month", "iyul"), pgettext_lazy("month", "avgust"), pgettext_lazy("month", "sentabr"),
    pgettext_lazy("month", "oktabr"), pgettext_lazy("month", "noyabr"), pgettext_lazy("month", "dekabr"),
)


@register.filter
def month_name(value) -> str:
    """date -> month name in the current language ('noyabr', 'ноябрь', 'November')."""
    try:
        return str(MONTHS[value.month - 1])
    except (AttributeError, IndexError, TypeError):
        return ""


@register.filter
def cat_label(value: str) -> str:
    return category_label(value)


@register.filter
def role_label(value: str) -> str:
    return _role_label(value)


@register.filter
def short_role(value: str) -> str:
    """Trade name without anything in brackets: 'Arrachi (Sawyer)' -> 'Arrachi'."""
    return role_label(value).split(" (")[0]


@register.filter
def role_list(values) -> str:
    """['ARRACHI', 'ROVERCHI'] -> 'Arrachi, Roverchi' in the current language."""
    return ", ".join(short_role(value) for value in (values or []))


@register.filter
def percent_of(value, total) -> int:
    number, whole = _to_decimal(value), _to_decimal(total)
    if not number or not whole:
        return 0
    return max(0, min(100, int(round(number / whole * 100))))


@register.simple_tag(takes_context=True)
def query_with(context, **kwargs) -> str:
    """Current query string with some keys replaced (empty value removes the key)."""
    params = context["request"].GET.copy()
    for key, value in kwargs.items():
        if value in ("", None):
            params.pop(key, None)
        else:
            params[key] = value
    params.pop("page", None)
    encoded = urlencode(sorted(params.lists()), doseq=True)
    return f"?{encoded}" if encoded else "?"


# ---------------------------------------------------------------------------
# Icons (outline, 1.5px, 24-unit grid) and the offcut figure
# ---------------------------------------------------------------------------
_ICON_PATHS = {
    "box": '<path d="M21 8v8a2 2 0 0 1-1 1.73l-7 4a2 2 0 0 1-2 0l-7-4A2 2 0 0 1 3 16V8a2 2 0 0 1 1-1.73l7-4a2 2 0 0 1 2 0l7 4A2 2 0 0 1 21 8z"/><path d="M3.3 7 12 12l8.7-5M12 22V12"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "minus": '<path d="M5 12h14"/>',
    "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    "chart": '<path d="M3 3v18h18"/><path d="M7 16v1M11 11v6M15 7v10M19 13v4"/>',
    "users": '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75"/>',
    "layers": '<path d="m12 2 10 5-10 5L2 7l10-5z"/><path d="m2 17 10 5 10-5M2 12l10 5 10-5"/>',
    "arrival": '<path d="M12 3v12"/><path d="m7 10 5 5 5-5"/><path d="M4 15v4a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-4"/>',
    "report": '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6M8 13h8M8 17h5"/>',
    "logout": '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><path d="m16 17 5-5-5-5M21 12H9"/>',
    "menu": '<path d="M4 6h16M4 12h16M4 18h16"/>',
    "search": '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
    "close": '<path d="M18 6 6 18M6 6l12 12"/>',
    "offcut": '<path d="M3 3h18v18H3z"/><path d="M3 13h8v8"/>',
    "back": '<path d="m12 19-7-7 7-7M19 12H5"/>',
    "download": '<path d="M12 3v12m-5-5 5 5 5-5M5 21h14"/>',
    "alert": '<path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/><path d="M12 9v4M12 17h.01"/>',
    "check": '<path d="M20 6 9 17l-5-5"/>',
    "dashboard": '<path d="M3 3h7v9H3zM14 3h7v5h-7zM14 12h7v9h-7zM3 16h7v5H3z"/>',
    "take": '<path d="M12 21V9"/><path d="m7 14 5-5 5 5"/><path d="M4 3h16"/>',
    "eye": '<path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7-10-7-10-7z"/><circle cx="12" cy="12" r="3"/>',
    "eye-off": '<path d="M10.6 5.2A9.9 9.9 0 0 1 12 5c6.4 0 10 7 10 7a18 18 0 0 1-2.4 3.3M6.1 6.3A18 18 0 0 0 2 12s3.6 7 10 7a9.6 9.6 0 0 0 4-.8"/><path d="M9.9 9.9a3 3 0 0 0 4.2 4.2"/><path d="m3 3 18 18"/>',
    "edit": '<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 1 1 3 3L7 19l-4 1 1-4z"/>',
}


@register.simple_tag
def icon(name: str, size: int = 16, css_class: str = "") -> str:
    paths = _ICON_PATHS.get(name, "")
    return format_html(
        '<svg class="{}" width="{}" height="{}" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{}</svg>',
        css_class,
        size,
        size,
        mark_safe(paths),  # noqa: S308 — constant, trusted markup
    )


@register.simple_tag
def offcut_figure(length_mm, width_mm, sheet_length=None, sheet_width=None, box_w: int = 64, box_h: int = 40) -> str:
    """
    The piece drawn to scale inside the outline of a full sheet, so a worker can
    see at a glance whether an offcut is big enough before walking to the rack.
    """
    piece = sorted((int(length_mm), int(width_mm)), reverse=True)
    has_sheet = bool(sheet_length and sheet_width)
    sheet = sorted((int(sheet_length), int(sheet_width)), reverse=True) if has_sheet else piece
    scale = min((box_w - 2) / sheet[0], (box_h - 2) / sheet[1])
    sw, sh = sheet[0] * scale, sheet[1] * scale
    pw, ph = max(piece[0] * scale, 2), max(piece[1] * scale, 2)
    ox, oy = (box_w - sw) / 2, (box_h - sh) / 2

    def n(value: float) -> str:
        return f"{value:.1f}"

    outline = (
        format_html(
            '<rect x="{}" y="{}" width="{}" height="{}" fill="none" stroke="#c7ccd4" stroke-width="1"/>',
            n(ox), n(oy), n(sw), n(sh),
        )
        if has_sheet
        else ""
    )
    sizes = {"length": piece[0], "width": piece[1], "sheet_length": sheet[0], "sheet_width": sheet[1]}
    if has_sheet:
        label = _("%(length)s × %(width)s mm qoldiq, list %(sheet_length)s × %(sheet_width)s mm") % sizes
    else:
        label = _("%(length)s × %(width)s mm qoldiq") % sizes
    return format_html(
        '<svg width="{}" height="{}" viewBox="0 0 {} {}" role="img" aria-label="{}">{}'
        '<rect x="{}" y="{}" width="{}" height="{}" fill="#f1e2cf" stroke="#9a5b22" stroke-width="1"/></svg>',
        box_w, box_h, box_w, box_h, label, outline, n(ox), n(oy + sh - ph), n(pw), n(ph),
    )
