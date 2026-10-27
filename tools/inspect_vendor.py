"""Inspect selected methods in an installed Innovate SDK DLL without executing it."""

import argparse
import sys
from pathlib import Path

import dnfile
from dncil.cil.body import CilMethodBody
from dncil.cil.body.reader import CilMethodBodyReaderBytes

DEFAULT_METHODS = ("AddPhysDevice", "AddBCG1Device", "GetAuxUnitMinMax", "CloseSerialPort")


def resolve_operand(pe, operand):
    if not hasattr(operand, "value"):
        return operand
    token = operand.value
    table, index = token >> 24, (token & 0xFFFFFF) - 1
    tables = {4: pe.net.mdtables.Field, 6: pe.net.mdtables.MethodDef, 10: pe.net.mdtables.MemberRef}
    if table in tables:
        return str(tables[table].rows[index].Name)
    if table == 0x70:
        return str(pe.net.user_strings.get(token & 0xFFFFFF))
    return operand


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dll", type=Path)
    parser.add_argument("--method", action="append", help="Method name; repeat to select several")
    args = parser.parse_args()
    methods = set(args.method or DEFAULT_METHODS)
    pe = dnfile.dnPE(str(args.dll))
    try:
        if pe.net is None:
            parser.error("The file has no .NET metadata")
        found = set()
        for row in pe.net.mdtables.MethodDef:
            name = str(row.Name)
            if name not in methods or not row.Rva:
                continue
            found.add(name)
            print(f"\nMETHOD {name}")
            body = CilMethodBody(CilMethodBodyReaderBytes(pe.get_data(row.Rva, 100000)))
            for instruction in body.instructions:
                operand = resolve_operand(pe, instruction.operand)
                print(f"{instruction.offset:04x} {str(instruction.opcode):16} {operand}")
        for name in sorted(methods - found):
            print(f"Method not found: {name}", file=sys.stderr)
    finally:
        pe.close()


if __name__ == "__main__":
    main()
