#!/usr/bin/python3

import asyncio
import colorsys
import os
import re

import aiohttp

from github_stats import Stats


################################################################################
# Helper Functions
################################################################################

def generate_output_folder() -> None:
    """
    Create the output folder if it does not already exist
    """
    if not os.path.isdir("generated"):
        os.mkdir("generated")


def tune_color_for_dark_background(hex_color: str) -> str:
    """
    Adjusts a hex color to ensure it is eye-friendly and readable on dark backgrounds
    by normalizing its lightness and saturation in the HSL space.
    """
    hex_color = hex_color.lstrip("#")
    if len(hex_color) != 6:
        return "#a5d6ff"  # Default eye-friendly blue fallback

    # Convert HEX to RGB (0-1 range)
    r, g, b = [int(hex_color[i:i+2], 16)/255.0 for i in (0, 2, 4)]
    
    # Convert RGB to HLS
    h, l, s = colorsys.rgb_to_hls(r, g, b)

    # Tune for dark background: ensure lightness is between 55% and 75% for vivid but readable colors
    l = max(0.55, min(l, 0.75))
    # Ensure saturation is vivid but not piercing
    s = max(0.50, min(s, 0.75))

    # Convert back to RGB
    r, g, b = colorsys.hls_to_rgb(h, l, s)
    
    # Convert back to HEX
    return f"#{int(r*255):02x}{int(g*255):02x}{int(b*255):02x}"


################################################################################
# Individual Image Generation Functions
################################################################################

async def generate_overview(s: Stats) -> None:
    """
    Generate an SVG badge with summary statistics
    :param s: Represents user's GitHub statistics
    """
    try:
        with open("templates/overview.svg", "r") as f:
            output = f.read()
    except FileNotFoundError:
        raise FileNotFoundError("Template file 'templates/overview.svg' not found. "
                                "Ensure the templates directory exists and contains the required SVG files.")

    output = re.sub("{{ name }}", await s.name, output)
    output = re.sub("{{ stars }}", f"{await s.stargazers:,}", output)
    output = re.sub("{{ forks }}", f"{await s.forks:,}", output)
    output = re.sub("{{ contributions }}", f"{await s.total_contributions:,}",
                    output)
    lines = await s.lines_changed
    changed = lines[0] + lines[1]
    output = re.sub("{{ lines_changed }}", f"{changed:,}", output)
    output = re.sub("{{ views }}", f"{await s.views:,}", output)
    output = re.sub("{{ repos }}", f"{len(await s.all_repos):,}", output)

    generate_output_folder()
    with open("generated/overview.svg", "w") as f:
        f.write(output)


async def generate_languages(s: Stats) -> None:
    """
    Generate an SVG badge with summary languages used
    :param s: Represents user's GitHub statistics
    """
    try:
        with open("templates/languages.svg", "r") as f:
            output = f.read()
    except FileNotFoundError:
        raise FileNotFoundError("Template file 'templates/languages.svg' not found. "
                                "Ensure the templates directory exists and contains the required SVG files.")

    progress = ""
    lang_list = ""
    sorted_languages = sorted((await s.languages).items(), reverse=True,
                              key=lambda t: t[1].get("size"))
    delay_between = 150
    for i, (lang, data) in enumerate(sorted_languages):
        color = data.get("color")
        color = tune_color_for_dark_background(color) if color else "#a5d6ff"
        
        ratio = [.98, .02]
        if data.get("prop", 0) > 50:
            ratio = [.99, .01]
        if i == len(sorted_languages) - 1:
            ratio = [1, 0]
        progress += (f'<span style="background-color: {color};'
                     f'width: {(ratio[0] * data.get("prop", 0)):0.3f}%;'
                     f'margin-right: {(ratio[1] * data.get("prop", 0)):0.3f}%;'
                     f'opacity: 0.85;" '
                     f'class="progress-item"></span>')

        # Two-column grid: open a row div on even indices, close on odd (or last)
        if i % 2 == 0:
            lang_list += '<div class="lang-row" style="display: flex; gap: 16px; margin-bottom: 6px;">\n'

        lang_list += f"""\
  <div class="lang-item" style="display: flex; align-items: center; gap: 7px; flex: 1; white-space: nowrap; font-size: 15.5px; animation-delay: {i * delay_between}ms;">
    <svg xmlns="http://www.w3.org/2000/svg" class="octicon" style="fill:{color}; opacity: 0.9; flex-shrink: 0;"
      viewBox="0 0 16 16" version="1.1" width="16" height="16">
      <path fill-rule="evenodd" d="M8 4a4 4 0 100 8 4 4 0 000-8z"></path>
    </svg>
    <span class="lang">{lang}</span>
    <span class="percent">({data.get("prop", 0):0.2f}%)</span>
  </div>
"""

        # Close row on odd index, or if this is the last item
        if i % 2 == 1 or i == len(sorted_languages) - 1:
            # Pad with empty cell if last row has only one item
            if i % 2 == 0:
                lang_list += '  <div class="lang-item" style="flex: 1;"></div>\n'
            lang_list += '</div>\n'

    output = re.sub(r"{{ progress }}", progress, output)
    output = re.sub(r"{{ lang_list }}", lang_list, output)

    generate_output_folder()
    with open("generated/languages.svg", "w") as f:
        f.write(output)


################################################################################
# Main Function
################################################################################

async def main() -> None:
    """
    Generate all badges
    """
    access_token = os.getenv("ACCESS_TOKEN")
    if not access_token:
        # access_token = os.getenv("GITHUB_TOKEN")
        raise Exception("A personal access token is required to proceed!")
    user = os.getenv("GITHUB_ACTOR")
    exclude_repos = os.getenv("EXCLUDED")
    exclude_repos = ({x.strip() for x in exclude_repos.split(",")}
                     if exclude_repos else None)
    exclude_langs = os.getenv("EXCLUDED_LANGS")
    exclude_langs = ({x.strip() for x in exclude_langs.split(",")}
                     if exclude_langs else None)
    consider_forked_repos = os.getenv("COUNT_STATS_FROM_FORKS", "").strip().lower() in ("1", "true", "yes")
    async with aiohttp.ClientSession() as session:
        s = Stats(user, access_token, session, exclude_repos=exclude_repos,
                  exclude_langs=exclude_langs,
                  consider_forked_repos=consider_forked_repos)
        await asyncio.gather(generate_languages(s), generate_overview(s))


if __name__ == "__main__":
    asyncio.run(main())
