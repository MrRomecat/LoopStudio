"""Умное перемешивание плейлиста: честное разнообразие переизданий.

Правила ниши:
- «не два трека одного артиста подряд» (артист — из тегов файлов);
- «закрепить первый трек» — сильное начало решает удержание;
- плейлист зациклен резиновым таймлайном, поэтому правило артистов
  проверяется и на стыке круга (последний ↔ первый).

Треки без тега артиста ничего не ограничивают.
"""

from __future__ import annotations

import random
from collections.abc import Sequence

_MAX_FIX_PASSES = 200


def _conflict(a: str | None, b: str | None) -> bool:
    return a is not None and b is not None and a.lower() == b.lower()


def _has_conflicts(artists: list[str | None], *, wrap: bool) -> bool:
    for i in range(1, len(artists)):
        if _conflict(artists[i - 1], artists[i]):
            return True
    return wrap and len(artists) > 2 and _conflict(artists[-1], artists[0])


def _count_conflicts(artists: list[str | None], *, wrap: bool) -> int:
    count = sum(1 for i in range(1, len(artists)) if _conflict(artists[i - 1], artists[i]))
    if wrap and len(artists) > 2 and _conflict(artists[-1], artists[0]):
        count += 1
    return count


def smart_shuffle(
    tracks: Sequence[tuple[str, str | None]],
    *,
    keep_first: bool = False,
    rng: random.Random | None = None,
) -> list[str]:
    """Перемешать плейлист, стараясь не ставить одного артиста подряд.

    :param tracks: (имя файла, артист или None) в текущем порядке.
    :param keep_first: первый трек остаётся на месте.
    Если развести артистов невозможно (например, все треки одного артиста),
    возвращается лучшее найденное перемешивание.
    """
    rng = rng or random.Random()
    names = [name for name, _ in tracks]
    artist_of = {name: artist for name, artist in tracks}
    if len(names) < 2:
        return list(names)

    head: list[str] = []
    body = list(names)
    if keep_first:
        head, body = [names[0]], list(names[1:])

    # неразводимый набор (у одного артиста больше половины позиций) —
    # идеала не существует, 200 дорогих проходов починки не нужны
    counts: dict[str, int] = {}
    for artist in artist_of.values():
        if artist is not None:
            counts[artist.lower()] = counts.get(artist.lower(), 0) + 1
    unfixable = bool(counts) and max(counts.values()) * 2 > len(names)
    passes = 3 if unfixable else _MAX_FIX_PASSES

    best: list[str] | None = None
    best_conflicts = len(names) + 2
    for _ in range(passes):
        rng.shuffle(body)
        candidate = head + body
        artists = [artist_of[n] for n in candidate]
        if not _has_conflicts(artists, wrap=True):
            return candidate
        # точечная починка: конфликтную позицию меняем с случайной подходящей
        for i in range(1, len(candidate)):
            if not _conflict(artists[i - 1], artists[i]):
                continue
            swap_candidates = list(range(1 if keep_first else 0, len(candidate)))
            rng.shuffle(swap_candidates)
            for j in swap_candidates:
                if j == i:
                    continue
                candidate[i], candidate[j] = candidate[j], candidate[i]
                artists = [artist_of[n] for n in candidate]
                if not _conflict(artists[i - 1], artists[i]):
                    break
                candidate[i], candidate[j] = candidate[j], candidate[i]
                artists = [artist_of[n] for n in candidate]
        conflicts = _count_conflicts(artists, wrap=True)
        if conflicts == 0:
            return candidate
        if conflicts < best_conflicts:  # реально лучшее, а не первое неудачное
            best, best_conflicts = list(candidate), conflicts
    return best if best is not None else list(names)
