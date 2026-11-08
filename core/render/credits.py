"""Кредиты артистов — «<имя> — кредиты.txt» рядом с готовым роликом.

Правило ниши: каждый артист сборника кредитуется в описании, иначе артисты
перестают давать музыку. Раньше блок собирался вручную из тегов за 20–30
минут; теперь готовый список «Исполнитель — Название» группируется по
артистам из тегов файлов (ffprobe их уже читает), с пометкой пустых тегов.
"""

from __future__ import annotations

from pathlib import Path

from core.media.probe import MediaInfo


def build_credits_text(tracks: list[MediaInfo]) -> str:
    """Собрать текст кредитов по уникальным трекам плейлиста.

    Треки без тега исполнителя собираются в отдельную секцию — автор сразу
    видит, какие файлы кредитовать вручную.
    """
    by_artist: dict[str, list[str]] = {}
    untagged: list[str] = []
    for info in tracks:
        title = info.title_tag or info.path.stem
        if info.artist:
            by_artist.setdefault(info.artist, []).append(title)
        else:
            untagged.append(info.path.name)

    out: list[str] = ["Музыка в этом видео:"]
    for artist in sorted(by_artist, key=str.lower):
        for title in by_artist[artist]:
            out.append(f"{artist} — {title}")
    if not by_artist:
        out.append("(теги исполнителей не заполнены ни у одного трека)")
    if untagged:
        out += [
            "",
            f"⚠ У {len(untagged)} трек(ов) не заполнен тег исполнителя — допишите кредиты вручную:",
        ]
        out.extend(f"• {name}" for name in untagged)
    out.append("")
    return "\n".join(out)


def unique_tracks(playlist: list[tuple[Path, MediaInfo]]) -> list[MediaInfo]:
    """Уникальные треки плейлиста в порядке первого появления."""
    seen: set[Path] = set()
    result: list[MediaInfo] = []
    for path, info in playlist:
        if path not in seen:
            seen.add(path)
            result.append(info)
    return result
