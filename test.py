import json
import os
from itertools import product
from pathlib import Path
from typing import Any, Union

config_file = Path("./config.json")




def load_config_file(path: Union[str, Path]):
	raw = json.loads(Path(path).read_text(encoding="utf-8"))
	if not isinstance(raw, list):
		raise ValueError("Settings file must contain a JSON array of objects.")

	parsed: list[dict[str, Any]] = []
	for i, item in enumerate(raw):
		if not isinstance(item, dict):
			raise ValueError(f"Settings entry at index {i} must be an object.")
		parsed.append(item)
	return parsed


def _generate_combos(params: dict) -> list:
    combos = []
    for combo in product(*params.values()):
        cfg = dict(zip(params.keys(), combo))
        combos.append(cfg)
    return combos

def generate_ior_tests(params: dict) -> dict:
    tests = {}
    for cfg in _generate_combos(params):
        name = f"ior.{cfg['clients']}-clients.{cfg['ppn']}-ppn.{cfg['blocksize']}-size.{cfg['xfersize']}-xfersize.{cfg['directio']}-directio.{cfg['api']}-api.{cfg['checksums']}-checksums.{cfg['operation']}"
        tests[name] = cfg
    return tests

def generate_mdtest_tests(params: dict) -> dict:
    tests = {}
    for cfg in _generate_combos(params):
        name = f"mdtest_{cfg['clients']}clients_{cfg['ppn']}ppn_{cfg['operation']}"
        tests[name] = cfg
    return tests



config_data = load_config_file(config_file)
ior_tests = config_data[0]["tests"]["ior"]

all_tests = generate_ior_tests(ior_tests)

for test in all_tests:
	print(f"{test}")


