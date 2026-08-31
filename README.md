Run any of the tools by being inside the folder then ```python tool_name.py```

## Image Grid Tool:
A small desktop app for putting images into a grid, captioning them, and getting a single image out the other end.

I kept making figure panels for reports by hand in PowerPoint, which was slow and looked slightly different every time. This does the same job in about thirty seconds and gives me the LaTeX for it as well.

Run ```python image_grid_composer.py```

You may need to install ```pip install PyQt6 Pillow```

### What it does
Load images by dragging them in, picking them from a file dialog, or pasting from the clipboard with Ctrl+V (screenshots work fine). Reorder them, delete the ones you don't want, and give each one a caption if you need one. You can also caption the whole panel

It picks a column count based on how many images you've loaded, but you can change it. If the last row doesn't fill up, you choose whether it sits left, centre, or right

Gap size, outer margin, background colour, and caption font sizes are all adjustable

### Getting it out

- **Save image** — PNG, JPG, or TIFF.
- **Copy to clipboard** — pastes straight into Word, Slides, or wherever.
- **Export LaTeX** — writes a `.tex` file plus a folder of the images.
  Full rows are `\hfill`-justified subfigures; a short last row is wrapped
  in `\makebox` so the alignment matches the exported image. Needs
  `graphicx` and `subcaption`. There's a checkbox for a standalone
  compilable document if you want to test it on its own.
