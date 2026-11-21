"""Refresh the portable Power Query source from the order-level extract."""
import argparse
import base64
import gzip
from pathlib import Path
from textwrap import wrap

ROOT = Path(__file__).resolve().parents[1]
MODEL = ROOT / 'Olist Operations.SemanticModel' / 'definition' / 'expressions.tmdl'


def embed(source, output):
    encoded = base64.b64encode(gzip.compress(source.read_bytes(), mtime=0)).decode("ascii")
    chunks = ",\n".join(f'\t\t\t\t"{chunk}"' for chunk in wrap(encoded, 4096))
    output.write_text(
        'expression OrdersCsvPath = "" meta [IsParameterQuery=true, Type="Text", IsParameterQueryRequired=false]\n'
        '\tlineageTag: 8a0cdb84-34fc-50a6-a274-faa1c8a46040\n\n'
        'expression EmbeddedOrders =\n'
        '\t\tBinary.Decompress(Binary.FromText(Text.Combine({\n'
        + chunks + '\n\t\t}), BinaryEncoding.Base64), Compression.GZip)\n'
        '\tlineageTag: e8be9739-2c8c-4043-9083-a9de7cfeecd8\n', encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=ROOT / 'data/orders.csv')
    embed(parser.parse_args().input, MODEL)
