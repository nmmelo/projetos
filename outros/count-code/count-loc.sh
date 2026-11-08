#!/usr/bin/env bash
#
# scripts/count-loc.sh
#
# Conta e detalha todas as linhas de código de um projeto.
#
# Uso:
#   ./scripts/count-loc.sh [caminho_do_projeto] [opções]
#
# Exemplos:
#   ./scripts/count-loc.sh portal
#   ./scripts/count-loc.sh /home/nmmelo/workspace/portal
#   ./scripts/count-loc.sh portal --files
#   ./scripts/count-loc.sh portal --dirs
#   ./scripts/count-loc.sh portal --json
#

exec python3 - "$@" << 'EOF'
import os
import sys
import time
import json
import argparse
from pathlib import Path

# Definição de linguagens e extensões com tipos de comentários
LANGUAGES = {
    ".php": {"name": "PHP", "single": ("//", "#"), "multi": ("/*", "*/")},
    ".phtml": {"name": "PHP", "single": ("//", "#"), "multi": ("/*", "*/")},
    ".js": {"name": "JavaScript", "single": ("//",), "multi": ("/*", "*/")},
    ".mjs": {"name": "JavaScript", "single": ("//",), "multi": ("/*", "*/")},
    ".cjs": {"name": "JavaScript", "single": ("//",), "multi": ("/*", "*/")},
    ".ts": {"name": "TypeScript", "single": ("//",), "multi": ("/*", "*/")},
    ".tsx": {"name": "TypeScript React", "single": ("//",), "multi": ("/*", "*/")},
    ".jsx": {"name": "JavaScript React", "single": ("//",), "multi": ("/*", "*/")},
    ".html": {"name": "HTML", "single": (), "multi": ("<!--", "-->")},
    ".htm": {"name": "HTML", "single": (), "multi": ("<!--", "-->")},
    ".css": {"name": "CSS", "single": (), "multi": ("/*", "*/")},
    ".scss": {"name": "SCSS", "single": ("//",), "multi": ("/*", "*/")},
    ".sass": {"name": "Sass", "single": ("//",), "multi": ("/*", "*/")},
    ".less": {"name": "Less", "single": ("//",), "multi": ("/*", "*/")},
    ".sql": {"name": "SQL", "single": ("--", "#"), "multi": ("/*", "*/")},
    ".sh": {"name": "Shell / Bash", "single": ("#",), "multi": ()},
    ".bash": {"name": "Shell / Bash", "single": ("#",), "multi": ()},
    ".zsh": {"name": "Shell / Bash", "single": ("#",), "multi": ()},
    ".py": {"name": "Python", "single": ("#",), "multi": ('"""', '"""')},
    ".json": {"name": "JSON", "single": (), "multi": ()},
    ".xml": {"name": "XML", "single": (), "multi": ("<!--", "-->")},
    ".yaml": {"name": "YAML", "single": ("#",), "multi": ()},
    ".yml": {"name": "YAML", "single": ("#",), "multi": ()},
    ".md": {"name": "Markdown", "single": (), "multi": ("<!--", "-->")},
    ".ini": {"name": "INI / Config", "single": (";", "#"), "multi": ()},
    ".conf": {"name": "INI / Config", "single": (";", "#"), "multi": ()},
    ".env": {"name": "Environment", "single": ("#",), "multi": ()},
    ".env.example": {"name": "Environment", "single": ("#",), "multi": ()},
    ".txt": {"name": "Plain Text", "single": (), "multi": ()},
    ".java": {"name": "Java", "single": ("//",), "multi": ("/*", "*/")},
    ".c": {"name": "C", "single": ("//",), "multi": ("/*", "*/")},
    ".cpp": {"name": "C++", "single": ("//",), "multi": ("/*", "*/")},
    ".h": {"name": "C/C++ Header", "single": ("//",), "multi": ("/*", "*/")},
    ".go": {"name": "Go", "single": ("//",), "multi": ("/*", "*/")},
    ".rs": {"name": "Rust", "single": ("//",), "multi": ("/*", "*/")},
    ".rb": {"name": "Ruby", "single": ("#",), "multi": ("=begin", "=end")},
}

IGNORED_DIRS = {
    ".git", ".svn", ".hg", "node_modules", "vendor", ".secrets",
    ".vscode", ".idea", "__pycache__", "dist", "build", "coverage",
    ".cache", "tmp", "temp", ".next", ".nuxt"
}

IGNORED_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".webp", ".svg",
    ".pdf", ".zip", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".7z",
    ".woff", ".woff2", ".ttf", ".eot", ".otf",
    ".mp3", ".mp4", ".mov", ".avi", ".mkv",
    ".exe", ".bin", ".so", ".dll", ".dylib", ".o", ".a",
    ".enc", ".key", ".pyc", ".pyo", ".class", ".db", ".sqlite", ".sqlite3"
}

def analyze_file(filepath, lang_info):
    code = blank = comment = 0
    single_c = lang_info["single"]
    multi_c = lang_info["multi"]
    in_multi = False

    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
    except Exception:
        return 0, 0, 0, 0

    for raw in lines:
        line = raw.strip()
        if not line:
            blank += 1
            continue

        if in_multi:
            comment += 1
            if multi_c and multi_c[1] in line:
                in_multi = False
            continue

        if multi_c and multi_c[0] in line and not (line.endswith(multi_c[1]) and line.count(multi_c[0]) == line.count(multi_c[1])):
            comment += 1
            in_multi = True
            continue

        if multi_c and line.startswith(multi_c[0]) and line.endswith(multi_c[1]):
            comment += 1
            continue

        if any(line.startswith(sc) for sc in single_c):
            comment += 1
            continue

        code += 1

    return len(lines), code, comment, blank

def format_num(val):
    return f"{val:,}".replace(",", " ")

def main():
    parser = argparse.ArgumentParser(
        description="Conta e detalha todas as linhas de código de um projeto.",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog="Exemplos:\n"
               "  ./scripts/count-loc.sh portal\n"
               "  ./scripts/count-loc.sh /home/nmmelo/workspace/portal\n"
               "  ./scripts/count-loc.sh portal --files\n"
               "  ./scripts/count-loc.sh portal --dirs\n"
               "  ./scripts/count-loc.sh portal --json"
    )
    parser.add_argument("path", nargs="?", default=".", help="Caminho do projeto (predefinição: pasta atual '.')")
    parser.add_argument("--files", "-f", action="store_true", help="Apresentar detalhe individual de cada ficheiro")
    parser.add_argument("--dirs", "-d", action="store_true", help="Apresentar detalhe agrupado por pasta")
    parser.add_argument("--json", action="store_true", help="Exportar resultados em formato JSON")
    parser.add_argument("--exclude", "-e", nargs="+", default=[], help="Pastas adicionais a excluir")

    args = parser.parse_args()

    project_path = Path(args.path).resolve()
    if not project_path.exists():
        print(f"Erro: O caminho '{args.path}' não existe.", file=sys.stderr)
        sys.exit(1)

    if not project_path.is_dir():
        print(f"Erro: O caminho '{args.path}' não é um diretório.", file=sys.stderr)
        sys.exit(1)

    start_time = time.time()
    ignored_dirs = IGNORED_DIRS | set(args.exclude)

    results_lang = {}
    file_list = []
    dir_results = {}

    total_files = total_code = total_comment = total_blank = total_lines = 0

    for root, dirs, files in os.walk(project_path):
        dirs[:] = [d for d in dirs if d not in ignored_dirs and not d.startswith(".")]

        rel_root = os.path.relpath(root, project_path)
        first_dir = rel_root.split(os.sep)[0] if rel_root != "." else "."

        for f in files:
            ext = os.path.splitext(f)[1].lower()
            if ext in IGNORED_EXTENSIONS or ext not in LANGUAGES:
                continue

            full_path = os.path.join(root, f)

            # Ignorar ligações simbólicas para não duplicar contagem
            if os.path.islink(full_path):
                continue

            lang_info = LANGUAGES[ext]
            lang_name = lang_info["name"]
            rel_file = os.path.relpath(full_path, project_path)

            lines, code, comment, blank = analyze_file(full_path, lang_info)

            # Acumular por linguagem
            if lang_name not in results_lang:
                results_lang[lang_name] = {"files": 0, "code": 0, "comment": 0, "blank": 0, "total": 0}
            results_lang[lang_name]["files"] += 1
            results_lang[lang_name]["code"] += code
            results_lang[lang_name]["comment"] += comment
            results_lang[lang_name]["blank"] += blank
            results_lang[lang_name]["total"] += lines

            # Acumular por pasta
            if first_dir not in dir_results:
                dir_results[first_dir] = {"files": 0, "code": 0, "comment": 0, "blank": 0, "total": 0}
            dir_results[first_dir]["files"] += 1
            dir_results[first_dir]["code"] += code
            dir_results[first_dir]["comment"] += comment
            dir_results[first_dir]["blank"] += blank
            dir_results[first_dir]["total"] += lines

            # Ficheiro individual
            file_list.append({
                "file": rel_file,
                "lang": lang_name,
                "code": code,
                "comment": comment,
                "blank": blank,
                "total": lines
            })

            total_files += 1
            total_code += code
            total_comment += comment
            total_blank += blank
            total_lines += lines

    elapsed = time.time() - start_time

    # Saída em JSON
    if args.json:
        payload = {
            "project": str(project_path),
            "elapsed_seconds": round(elapsed, 4),
            "summary": {
                "total_files": total_files,
                "total_code_lines": total_code,
                "total_comment_lines": total_comment,
                "total_blank_lines": total_blank,
                "total_lines": total_lines,
            },
            "by_language": results_lang,
            "by_directory": dir_results,
        }
        if args.files:
            payload["files"] = file_list
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return

    # Cabeçalho
    print()
    print("=" * 78)
    print(f" Análise de Linhas de Código: {project_path.name}")
    print(f" Caminho: {project_path}")
    print("=" * 78)
    print()

    # Tabela por Linguagem
    print("┌" + "─" * 20 + "┬" + "─" * 10 + "┬" + "─" * 12 + "┬" + "─" * 12 + "┬" + "─" * 10 + "┬" + "─" * 12 + "┐")
    print(f"│ {'Linguagem':<18} │ {'Ficheiros':>8} │ {'Código':>10} │ {'Comentários':>10} │ {'Branco':>8} │ {'Total':>10} │")
    print("├" + "─" * 20 + "┼" + "─" * 10 + "┼" + "─" * 12 + "┼" + "─" * 12 + "┼" + "─" * 10 + "┼" + "─" * 12 + "┤")

    sorted_langs = sorted(results_lang.items(), key=lambda x: x[1]["code"], reverse=True)
    for lang, d in sorted_langs:
        print(f"│ {lang:<18} │ {format_num(d['files']):>8} │ {format_num(d['code']):>10} │ {format_num(d['comment']):>10} │ {format_num(d['blank']):>8} │ {format_num(d['total']):>10} │")

    print("├" + "─" * 20 + "┼" + "─" * 10 + "┼" + "─" * 12 + "┼" + "─" * 12 + "┼" + "─" * 10 + "┼" + "─" * 12 + "┤")
    print(f"│ {'TOTAL':<18} │ {format_num(total_files):>8} │ {format_num(total_code):>10} │ {format_num(total_comment):>10} │ {format_num(total_blank):>8} │ {format_num(total_lines):>10} │")
    print("└" + "─" * 20 + "┴" + "─" * 10 + "┴" + "─" * 12 + "┴" + "─" * 12 + "┴" + "─" * 10 + "┴" + "─" * 12 + "┘")
    print()

    # Tabela por Diretório (se --dirs solicitado)
    if args.dirs and dir_results:
        print(" Detalhe por Pasta:")
        print("┌" + "─" * 24 + "┬" + "─" * 10 + "┬" + "─" * 12 + "┬" + "─" * 12 + "┬" + "─" * 12 + "┐")
        print(f"│ {'Pasta':<22} │ {'Ficheiros':>8} │ {'Código':>10} │ {'Branco':>10} │ {'Total':>10} │")
        print("├" + "─" * 24 + "┼" + "─" * 10 + "┼" + "─" * 12 + "┼" + "─" * 12 + "┼" + "─" * 12 + "┤")
        for dname, dd in sorted(dir_results.items(), key=lambda x: x[1]["code"], reverse=True):
            display_d = (dname[:19] + "...") if len(dname) > 22 else dname
            print(f"│ {display_d:<22} │ {format_num(dd['files']):>8} │ {format_num(dd['code']):>10} │ {format_num(dd['blank']):>10} │ {format_num(dd['total']):>10} │")
        print("└" + "─" * 24 + "┴" + "─" * 10 + "┴" + "─" * 12 + "┴" + "─" * 12 + "┴" + "─" * 10 + "┴" + "─" * 12 + "┘")
        print()

    # Top Ficheiros (se --files solicitado)
    if args.files and file_list:
        print(" Top 20 Maiores Ficheiros de Código:")
        sorted_files = sorted(file_list, key=lambda x: x["code"], reverse=True)[:20]
        print("┌" + "─" * 46 + "┬" + "─" * 14 + "┬" + "─" * 10 + "┬" + "─" * 10 + "┐")
        print(f"│ {'Ficheiro':<44} │ {'Linguagem':<12} │ {'Código':>8} │ {'Total':>8} │")
        print("├" + "─" * 46 + "┼" + "─" * 14 + "┼" + "─" * 10 + "┼" + "─" * 10 + "┤")
        for fi in sorted_files:
            fname = fi["file"]
            if len(fname) > 44:
                fname = "..." + fname[-41:]
            print(f"│ {fname:<44} │ {fi['lang'][:12]:<12} │ {format_num(fi['code']):>8} │ {format_num(fi['total']):>8} │")
        print("└" + "─" * 46 + "┴" + "─" * 14 + "┴" + "─" * 10 + "┴" + "─" * 10 + "┘")
        print()

    # Estatísticas e Métricas
    if total_lines > 0:
        pct_code = (total_code / total_lines) * 100
        pct_comm = (total_comment / total_lines) * 100
        pct_blank = (total_blank / total_lines) * 100
        print(f" Distribuição : {pct_code:.1f}% código  |  {pct_comm:.1f}% comentários  |  {pct_blank:.1f}% linhas em branco")

    largest_file = max(file_list, key=lambda x: x["code"]) if file_list else None
    if largest_file:
        print(f" Maior código : {largest_file['file']} ({format_num(largest_file['code'])} linhas de código)")

    print(f" Tempo decorr.: {elapsed:.3f} segundos")
    print("=" * 78)
    print()

if __name__ == "__main__":
    main()
EOF
