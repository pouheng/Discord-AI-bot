import markdown
import pathlib

src = pathlib.Path(__file__).parent / "spec.md"
out = pathlib.Path(__file__).parent / "spec.html"

md_text = src.read_text(encoding="utf-8")
body = markdown.markdown(md_text, extensions=["tables", "fenced_code", "toc", "sane_lists"])

css = """
body{font-family:'Segoe UI','Microsoft JhengHei',sans-serif;max-width:960px;margin:2rem auto;padding:0 1.5rem;line-height:1.7;color:#222}
h1,h2,h3{border-bottom:2px solid #eee;padding-bottom:.3rem;margin-top:2rem}
h1{color:#b00020} h2{color:#0d47a1} h3{color:#00695c}
table{border-collapse:collapse;width:100%;margin:1rem 0}
th,td{border:1px solid #ddd;padding:.5rem .75rem;text-align:left;font-size:.95rem}
th{background:#f5f5f5}
code{background:#f0f0f0;padding:.1rem .3rem;border-radius:3px;font-family:Consolas,monospace}
pre{background:#263238;color:#eee;padding:1rem;border-radius:6px;overflow-x:auto}
pre code{background:none;color:#eee}
blockquote{border-left:4px solid #b00020;margin:1rem 0;padding:.3rem 1rem;background:#fff5f5}
.toc{background:#fafafa;border:1px solid #eee;padding:1rem 1.5rem;border-radius:6px;float:right;margin-left:1.5rem;max-width:280px}
.toc ul{list-style:none;padding-left:.5rem}
a{color:#0d47a1}
"""

html = f"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<title>招式對撞系統規格書</title>
<style>{css}</style>
</head>
<body>
{body}
</body>
</html>"""

out.write_text(html, encoding="utf-8")
print("spec.html 已產生:", out)
