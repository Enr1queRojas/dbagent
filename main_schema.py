"""Inspect retrieved context locally, without Ollama or SQL Server calls."""
import argparse
import json
from pathlib import Path
import sys

import yaml
from core.schema_retriever import SchemaRetriever


def main():
    parser = argparse.ArgumentParser(description='Recuperar contexto del catálogo local')
    parser.add_argument('question')
    parser.add_argument('--schema-dir', type=Path)
    parser.add_argument('--config', type=Path)
    args = parser.parse_args()
    try:
        result = SchemaRetriever(args.schema_dir, args.config).retrieve(args.question)
        print(json.dumps(json.loads(result.context), ensure_ascii=False, indent=2))
        print(f'Objetos: {len(result.selected_objects)} | Contexto compacto: {len(result.context)} caracteres', file=sys.stderr)
        for warning in result.warnings:
            print(f'AVISO: {warning}', file=sys.stderr)
        return 0
    except (ValueError, OSError, yaml.YAMLError) as exc:
        print(f'No se pudo recuperar el esquema: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
