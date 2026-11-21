"""
Utility script to inspect Angebotstemplate.pptx structure in VS Code.
Run this first to verify slide indices, shape names, and placeholder texts.
"""
import sys
from pathlib import Path
from pptx import Presentation


def inspect_pptx(template_path: str | Path) -> None:
    """Prints a detailed map of all slides, shapes, and text placeholders."""
    path = Path(template_path)
    if not path.exists():
        print(f"Error: File not found at {path}")
        return

    prs = Presentation(str(path))
    print(f"=== Inspecting: {path.name} (Total Slides: {len(prs.slides)}) ===\n")

    for idx, slide in enumerate(prs.slides):
        layout_name = slide.slide_layout.name if slide.slide_layout else "Unknown"
        print(f"--- Slide [{idx}] (Layout: {layout_name}) ---")

        for shape in slide.shapes:
            shape_type = shape.shape_type
            name = shape.name
            pos = f"left={shape.left}, top={shape.top}, w={shape.width}, h={shape.height}"

            if shape.has_text_frame:
                preview = shape.text_frame.text.replace("\n", " | ").strip()
                if len(preview) > 80:
                    preview = preview[:77] + "..."
                print(f"  [Text] name='{name}' | {pos} | text='{preview}'")
            elif shape.has_table:
                rows = len(shape.table.rows)
                cols = len(shape.table.columns)
                print(f"  [Table] name='{name}' ({rows}x{cols}) | {pos}")
            else:
                print(f"  [Shape:{shape_type}] name='{name}' | {pos}")
        print()


if __name__ == "__main__":
    # Usage: python inspect_template.py path/to/Angebotstemplate.pptx
    target = sys.argv[1] if len(sys.argv) > 1 else Path(__file__).with_name("Angebotstemplate.pptx")
    inspect_pptx(target)
