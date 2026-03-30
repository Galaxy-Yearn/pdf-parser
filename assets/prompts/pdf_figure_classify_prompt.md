You are reviewing a figure crop extracted from a scientific PDF.
The crop has already passed a heuristic filter, but it may still be junk.
Return ONLY a JSON object with keys:
- kind: one of ["panel_label","figure_title","chart_plot","heatmap","micrograph_photo","schematic_diagram","table","decorative","unknown"]
- keep: boolean
- title: a short 3-10 word label for this crop only
- summary: if keep=true, 1-2 sentences describing this crop only; if keep=false, briefly explain why it should be dropped
Guidelines:
- panel_label: mostly a single letter/number like 'A'/'B'.
- figure_title: mostly caption text.
- chart_plot: scatter/line/bar plot.
- heatmap: 2D colored grid map.
- micrograph_photo: microscopy image/photo.
- schematic_diagram: device schematic/geometry diagram.
- decorative: icons/logos/headers.
- Do not restate the full shared figure caption as the summary.
- Use the shared caption only as context to better understand this specific crop.
- If this crop is one panel of a larger multi-panel figure, summarize the visible panel content, not the whole figure.
Crop ID: <<CROP_ID>>
Figure Tag: <<FIGURE_TAG>>
<<IF:CAPTION_TEXT>>Shared Caption: <<CAPTION_TEXT>>
<<ENDIF:CAPTION_TEXT>>Hint: <<PROMPT_HINT>>
