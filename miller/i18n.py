"""gettext catalog for visible Miller Columns copy.

English msgids live in the Python sources so a missing catalog still
renders. Dispatch maps in the column menu keep those msgids as keys;
``_()`` runs only when the ``Gtk.MenuItem`` is built, so a Portuguese
label cannot miss its handler.

Importing this module does not load a catalog. The extension calls
``install()`` at startup. Tests stay on ``NullTranslations`` unless they
call ``install`` themselves.

Add a language:

1. ``translations/<lang>.po``
2. ``msgfmt -o translations/<lang>/LC_MESSAGES/miller.mo translations/<lang>.po``
3. commit both files

``pt_BR`` resolves to ``pt``. There is no ``en.po``: the msgid is English.
"""

from __future__ import annotations

import gettext
import os

_DOMAIN = "miller"
_LOCALEDIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "translations"))
_translator = gettext.NullTranslations()

__all__ = ["_", "ngettext", "install", "reset"]


def _(message: str) -> str:
    """Return ``message`` translated for the installed catalog.

    Missing keys and ``NullTranslations`` return ``message`` unchanged.

    Args:
        message: English msgid.

    Returns:
        The catalog string, or ``message``.

    @tags: #subject/i18n #action/read #type/helper #scope/shared #side-effect/pure-function
    """
    return _translator.gettext(message)


def ngettext(singular: str, plural: str, n: int) -> str:
    """Return the plural form for ``n`` from the installed catalog.

    Args:
        singular: English msgid for one.
        plural: English msgid for other counts.
        n: Count that selects the form.

    Returns:
        The catalog string, or ``singular``/``plural`` when none is loaded.

    @tags: #subject/i18n #action/read #type/helper #scope/shared #side-effect/pure-function
    """
    return _translator.ngettext(singular, plural, n)


def install(languages: list[str] | None = None) -> None:
    """Load the catalog for ``languages``, or the process locale.

    ``fallback=True`` so a missing file returns the msgid instead of
    raising. ``languages=None`` lets gettext read ``LANGUAGE``, ``LC_ALL``,
    ``LC_MESSAGES``, and ``LANG``.

    Args:
        languages: Explicit locale tags, or None for the environment.

    @tags: #subject/i18n #action/read #action/render #type/helper #scope/shared #side-effect/filesystem #lifecycle/init
    """
    global _translator
    _translator = gettext.translation(
        _DOMAIN,
        localedir=_LOCALEDIR,
        languages=languages,
        fallback=True,
    )


def reset() -> None:
    """Restore ``NullTranslations`` so later lookups return the msgid.

    Tests call this after ``install`` so a Portuguese case cannot leak
    into the next test.

    @tags: #subject/i18n #action/clear #type/helper #scope/shared #lifecycle/destroy
    """
    global _translator
    _translator = gettext.NullTranslations()
