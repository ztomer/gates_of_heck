#!/bin/bash
# analyze_trace.sh — Parse xctrace .trace file into a ranked hotspot report
#
# Usage:
#   ./analyze_trace.sh <trace_file.trace> [output_report.txt]
#
# Requires: xctrace (Xcode command line tools), python3
# Output: Sorted symbol table by self-time, saved to <trace>.hotspots.txt
#   (or the specified output file)

set -euo pipefail

TRACE_FILE="${1:-}"
if [ -z "$TRACE_FILE" ]; then
    echo "Usage: $0 <trace_file.trace> [output_report.txt]"
    echo ""
    echo "Example:"
    echo "  $0 /tmp/hotspot_20260627_235900/profile.trace"
    exit 1
fi

if [ ! -e "$TRACE_FILE" ]; then
    echo "Error: Trace file not found: $TRACE_FILE"
    exit 1
fi

REPORT="${2:-${TRACE_FILE%.trace}.hotspots.txt}"
XML_DUMP="${TRACE_FILE%.trace}.export.xml"

echo "=== CadGoose Trace Analyzer ==="
echo "Trace:  $TRACE_FILE"
echo "Report: $REPORT"
echo ""

# ── Step 1: Export trace to XML ──────────────────────────────────────────────
echo "[1/3] Exporting trace to XML..."
if ! xctrace export \
        --input "$TRACE_FILE" \
        --xpath '//trace-toc' \
        --output - \
        > "${XML_DUMP}.toc" 2>/dev/null; then
    echo "  WARNING: xctrace export failed. Trying alternative export..."
fi

# Export the Time Profiler table
if xctrace export \
        --input "$TRACE_FILE" \
        --xpath '//profile-data' \
        --output "$XML_DUMP" \
        2>/dev/null; then
    echo "  XML export: OK ($XML_DUMP)"
else
    echo "  NOTE: Full XML export not available; trying sample-based fallback..."
    # Fallback: use xctrace analyze if export fails
    xctrace analyze \
        --input "$TRACE_FILE" \
        2>/dev/null > "$XML_DUMP" || true
fi

# ── Step 2: Parse & rank with python3 ────────────────────────────────────────
echo "[2/3] Parsing symbols and ranking by self-time..."

python3 - "$XML_DUMP" "$REPORT" "$TRACE_FILE" << 'PYEOF'
import sys
import os
import re
import xml.etree.ElementTree as ET
from collections import defaultdict

xml_file   = sys.argv[1]
report     = sys.argv[2]
trace_path = sys.argv[3]

lines = []
total_samples = 0

# ── Try parsing the XML dump ──────────────────────────────────────────────────
if os.path.exists(xml_file) and os.path.getsize(xml_file) > 100:
    try:
        content = open(xml_file).read()
        # xctrace XML can be large; truncate if needed for parsing
        if len(content) > 50_000_000:
            content = content[:50_000_000]

        root = ET.fromstring(content)

        # Try to find frame/sample data in various xctrace XML schemas
        sym_counts = defaultdict(int)

        # Schema 1: <row><sample-self>N</sample-self><frame><symbol>FOO</symbol></frame></row>
        for row in root.iter('row'):
            self_el = row.find('sample-self')
            frame_el = row.find('.//frame/symbol')
            if self_el is not None and frame_el is not None:
                try:
                    n = int(self_el.text or '0')
                    sym_counts[frame_el.text or '(unknown)'] += n
                    total_samples += n
                except ValueError:
                    pass

        # Schema 2: <frame weight="N">symbol</frame>
        if not sym_counts:
            for frame in root.iter('frame'):
                weight = frame.get('weight') or frame.get('self-weight', '0')
                sym = frame.text or frame.get('name', '(unknown)')
                try:
                    n = int(weight)
                    sym_counts[sym] += n
                    total_samples += n
                except ValueError:
                    pass

        if sym_counts:
            ranked = sorted(sym_counts.items(), key=lambda x: -x[1])
            lines.append(f"# Hotspot Report — {os.path.basename(trace_path)}")
            lines.append(f"# Total samples: {total_samples}")
            lines.append(f"# {'Self%':>7}  {'Self':>7}  Symbol")
            lines.append("-" * 80)
            for sym, cnt in ranked[:60]:
                pct = 100.0 * cnt / total_samples if total_samples else 0
                lines.append(f"  {pct:6.2f}%  {cnt:>7}  {sym}")
        else:
            lines.append("# NOTE: Could not extract symbol table from XML export.")
            lines.append("# The trace may use a schema version not recognized by this parser.")
            lines.append("# Open in Instruments for visual analysis: open \"" + trace_path + "\"")

    except ET.ParseError as e:
        lines.append(f"# XML parse error: {e}")
        lines.append("# Open in Instruments: open \"" + trace_path + "\"")
else:
    lines.append("# No XML data available.")
    lines.append("# Open in Instruments: open \"" + trace_path + "\"")

output = "\n".join(lines)
print(output)
with open(report, 'w') as f:
    f.write(output + "\n")
PYEOF

# ── Step 3: Summary ───────────────────────────────────────────────────────────
echo "[3/3] Done."
echo ""
echo "Report: $REPORT"
echo "Open trace in Instruments: open \"$TRACE_FILE\""
echo ""
# Show top 20 lines
if [ -f "$REPORT" ]; then
    head -30 "$REPORT"
fi
