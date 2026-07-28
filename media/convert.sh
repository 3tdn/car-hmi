#!/bin/bash

for file in *.avi; do
    # Bỏ qua nếu không có file AVI nào
    [ -f "$file" ] || continue

    output="${file%.avi}.mp4"

    echo "Converting: $file -> $output"

    ffmpeg -i "$file" \
        -c:v libx264 \
        -preset fast \
        -crf 23 \
        -movflags +faststart \
        "$output"
done

echo "Done!"
