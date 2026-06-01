# F:\MOFCNM\pack_code.py
# 运行后生成 code_bundle.json，把它发到新对话里

import json
from pathlib import Path

NL = chr(10)

FILES = [
    "src/main.py",
    "src/ui.py",
    "src/extractor.py",
    "src/ai_client.py",
    "src/schema_loader.py",
    "src/database.py",
    "src/exporter.py",
    "build.spec",
    "setup.iss",
    "requirements.txt",
    "Readme.yaml",
]

base = Path(__file__).parent
bundle = {}

for rel_path in FILES:
    full_path = base / rel_path
    if not full_path.exists():
        print("✗ 未找到：" + rel_path)
        continue
    try:
        with open(full_path, encoding="utf-8") as f:
            bundle[rel_path] = f.read()
        print("✓ " + rel_path)
    except UnicodeDecodeError:
        try:
            with open(full_path, encoding="gbk") as f:
                bundle[rel_path] = f.read()
            print("✓ " + rel_path + "  (GBK)")
        except Exception as e:
            print("✗ 读取失败：" + rel_path + "  " + str(e))

output = base / "code_bundle.json"
with open(output, "w", encoding="utf-8") as f:
    json.dump(bundle, f, ensure_ascii=False, indent=2)

print(NL + "打包完成，共 " + str(len(bundle)) + " 个文件")
print("输出文件：" + str(output))
print("文件大小：" + str(round(output.stat().st_size / 1024, 1)) + " KB")
