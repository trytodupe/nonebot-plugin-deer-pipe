import os
import nonebot_plugin_localstore as localstore
import secrets

from .constants import (
    ASSETS_FONT,
    ASSETS_IMG_AVATAR,
    ASSETS_IMG_CHECK,
    ASSETS_IMG_DEERPIPE,
)
from PIL import Image, ImageDraw
from calendar import monthcalendar
from datetime import datetime
from io import BytesIO


def gen_calendar(
    now: datetime, records: dict[int, int], name: str, avatar: bytes | None
):
    """
    Generate calendar image

    :param now: Current time
    :param records: dict[day, count]
    :param avatar: Optional user avatar
    :return: Image bytes
    """
    # Generate current month calendar
    cld = monthcalendar(now.year, now.month)

    # Image size & calendar cell size
    IMG_W, IMG_H = 700, 100 * (len(cld) + 1)
    CELL_W, CELL_H = 100, 100

    # Create image
    img = Image.new("RGBA", (IMG_W, IMG_H), "white")
    drw = ImageDraw.Draw(img)

    # Draw avatar
    if avatar is None:
        img.paste(ASSETS_IMG_AVATAR, (10, 10))
    else:
        avatar_img = Image.open(BytesIO(avatar)).convert("RGBA").resize((80, 80))
        img.paste(avatar_img, (10, 10))

    # Draw calendar info text
    ASSETS_FONT.draw(
        drw, (100, 10), f"{now.year}-{now.month:02} 🦌签到日历", fill="black"
    )
    ASSETS_FONT.draw(drw, (100, 40), f"@{name}", fill="black")

    # Draw cells
    for week_idx, week in enumerate(cld):
        for day_idx, day in enumerate(week):
            # Skip empty calendar cell
            if day == 0:
                continue

            # Cell position
            x0, y0 = day_idx * CELL_W, (week_idx + 1) * CELL_H

            # Draw cell
            img.paste(ASSETS_IMG_DEERPIPE, (x0, y0))
            ASSETS_FONT.draw(drw, (x0 + 5, y0 + CELL_H - 35), str(day), fill="black")

            # Skip day if not deered or count is zero
            if day not in records or records[day] == 0:
                continue

            # Draw check
            img.paste(ASSETS_IMG_CHECK, (x0, y0), ASSETS_IMG_CHECK)

            # Draw count text for all non-1 values (including negatives)
            if records[day] != 1:
                txt = "x999+" if records[day] > 999 else f"x{records[day]}"
                tlen = ASSETS_FONT.get_width(txt, size=20)
                ASSETS_FONT.draw(
                    drw,
                    (x0 + CELL_W - tlen - 5, y0 + CELL_H - 25),
                    txt,
                    size=20,
                    fill="red",
                    stroke_width=1,
                )

    # Export image to bytes
    img_bytes = BytesIO()
    img.save(img_bytes, format="PNG")

    # Save image for development
    if os.getenv("DEER_PIPE_DEV") is not None:
        img_path = localstore.get_plugin_cache_file(f"{secrets.token_hex()}.png")
        img.save(img_path)

    # Return bytes
    return img_bytes.getvalue()


def _draw_rank_side(
    img: Image.Image,
    drw: ImageDraw.ImageDraw,
    *,
    origin_x: int,
    title: str,
    rank: list[tuple[str, bytes | None, int]],
    title_fill: str,
):
    title_width = ASSETS_FONT.get_width(title, size=30)
    ASSETS_FONT.draw(
        drw,
        (origin_x + 225 - title_width / 2, 80),
        title,
        size=30,
        fill=title_fill,
        stroke_width=0.5,
    )

    for idx, (name, avatar, count) in enumerate(rank):
        row_y = 130 + idx * 90

        if avatar is None:
            img.paste(ASSETS_IMG_AVATAR, (origin_x + 10, row_y))
        else:
            avatar_img = Image.open(BytesIO(avatar)).convert("RGBA").resize((80, 80))
            img.paste(avatar_img, (origin_x + 10, row_y))

        ASSETS_FONT.draw(
            drw,
            (origin_x + 100, row_y + 10),
            f"@{name}",
            fill="black",
        )
        ASSETS_FONT.draw(
            drw,
            (origin_x + 100, row_y + 45),
            f"x{count}",
            fill=title_fill,
            stroke_width=0.5,
        )


def gen_rank(
    top_rank: list[tuple[str, bytes | None, int]],
    bottom_rank: list[tuple[str, bytes | None, int]],
):
    """
    Generate rank image

    :param top_rank: Top 5 rank list
    :param bottom_rank: Bottom 5 rank list
    :return: Image bytes
    """
    # Image size
    IMG_W = 900
    IMG_H = max(len(top_rank), len(bottom_rank), 1) * 90 + 180

    # Create image
    img = Image.new("RGBA", (IMG_W, IMG_H), "white")
    drw = ImageDraw.Draw(img)

    # Draw title
    tlen = ASSETS_FONT.get_width("本月Top5🦌榜", size=50)
    ASSETS_FONT.draw(
        drw,
        (IMG_W / 2 - tlen / 2, 25),
        "本月Top5🦌榜",
        size=50,
        fill="red",
        stroke_width=1,
    )

    # Draw rank columns
    _draw_rank_side(
        img,
        drw,
        origin_x=0,
        title="正榜 Top5",
        rank=top_rank,
        title_fill="red",
    )
    _draw_rank_side(
        img,
        drw,
        origin_x=450,
        title="负榜 Bottom5",
        rank=bottom_rank,
        title_fill="#1565c0",
    )

    # Export image to bytes
    img_bytes = BytesIO()
    img.save(img_bytes, format="PNG")

    # Save image for development
    if os.getenv("DEER_PIPE_DEV") is not None:
        img_path = localstore.get_plugin_cache_file(f"{secrets.token_hex()}.png")
        img.save(img_path)

    # Return bytes
    return img_bytes.getvalue()
