"""
canoe_mix.py - Concrete Mix Calculation & Batching Engine
Loads ingredient database and mix proportions, predicts density and strength,
and outputs batching weights scaled to target volume.
"""

import json
import math
import sys
import os

WATER_DENSITY_LBCFT = 62.427  # lb/cu ft

def load_json(filename):
    if not os.path.exists(filename):
        print(f"Error: Required file '{filename}' not found.")
        sys.exit(1)
    with open(filename, "r", encoding="utf-8") as f:
        return json.load(f)

def calculate_mix(db, mix, target_volume_cuft=1.0):
    w_cm = float(mix.get("w_cm", 0.25))
    air_pct = float(mix.get("air_pct", 10.8))
    bound_water_factor = float(mix.get("bound_water_factor", 0.24))
    cal_factor = float(mix.get("strength_calibration_factor", 1.0))
    
    dry_blend_input = mix.get("dry_blend", [])
    admixtures_input = mix.get("admixtures", [])

    if not dry_blend_input:
        raise ValueError("No 'dry_blend' array provided in mix config!")

    # 1. Normalize dry blend percentages
    tot_input_pct = sum(item.get("pct", 0.0) for item in dry_blend_input)
    if tot_input_pct <= 0:
        raise ValueError("Total dry blend percentage must be > 0.")

    dry_materials = []
    for item in dry_blend_input:
        mat_id = item["id"]
        if mat_id not in db:
            raise ValueError(f"Material ID '{mat_id}' not found in database!")
        info = db[mat_id]
        
        norm_pct = (item.get("pct", 0.0) / tot_input_pct) * 100.0
        dry_materials.append({
            "id": mat_id,
            "name": info.get("name", mat_id),
            "pct": norm_pct,
            "sg": info.get("sg", 1.0),
            "is_cm": info.get("is_cm", False),
            "efficiency": info.get("efficiency", 1.0),
            "d50_microns": info.get("d50_microns", 500.0)
        })

    # 2. Base calculations (100 lb dry batch baseline)
    base_dry_wt = 100.0
    cm_wt = sum(base_dry_wt * (m["pct"] / 100.0) for m in dry_materials if m["is_cm"])
    target_water_wt = cm_wt * w_cm

    # 3. Admixtures processing
    admixtures = []
    total_adm_wt = 0.0
    adm_solids_wt = 0.0
    adm_water_wt = 0.0

    for item in admixtures_input:
        adm_id = item["id"]
        if adm_id not in db:
            raise ValueError(f"Admixture ID '{adm_id}' not found in database!")
        info = db[adm_id]
        dose = item.get("dose_cwt", 0.0)
        sg = info.get("sg", 1.0)
        solids_pct = info.get("solids_pct", 0.0)

        if dose > 0:
            wt = (cm_wt / 100.0) * dose * (0.065198 * sg)
            solids = wt * (solids_pct / 100.0)
            water = wt - solids

            total_adm_wt += wt
            adm_solids_wt += solids
            adm_water_wt += water

            admixtures.append({
                "id": adm_id,
                "name": info.get("name", adm_id),
                "dose_cwt": dose,
                "base_wt": wt,
                "solids_wt": solids,
                "water_wt": water,
                "sg": sg
            })

    # 4. Volumetric Analysis
    dry_vols = [(base_dry_wt * (m["pct"] / 100.0)) / (m["sg"] * WATER_DENSITY_LBCFT) for m in dry_materials]
    tot_dry_vol = sum(dry_vols)
    tot_water_vol = target_water_wt / WATER_DENSITY_LBCFT
    tot_adm_vol = sum(a["base_wt"] / (a["sg"] * WATER_DENSITY_LBCFT) for a in admixtures)

    unit_batch_vol = (tot_dry_vol + tot_water_vol + tot_adm_vol) / (1.0 - (air_pct / 100.0))
    scale = target_volume_cuft / unit_batch_vol

    # 5. Scaled Quantities
    scaled_dry = []
    for m in dry_materials:
        scaled_dry.append({
            "name": m["name"],
            "wt": (base_dry_wt * (m["pct"] / 100.0)) * scale,
            "pct": m["pct"],
            "is_cm": m["is_cm"]
        })

    scaled_cm_wt = cm_wt * scale
    scaled_target_water = target_water_wt * scale
    scaled_adm_water = adm_water_wt * scale
    net_added_water = max(0.0, scaled_target_water - scaled_adm_water)

    scaled_admixtures = []
    for a in admixtures:
        scaled_admixtures.append({
            "name": a["name"],
            "wt": a["base_wt"] * scale,
            "dose_cwt": a["dose_cwt"]
        })

    # 6. Densities (lb/ft^3)
    total_wet_wt = (base_dry_wt * scale) + (target_water_wt * scale) + (total_adm_wt * scale)
    wet_density = total_wet_wt / target_volume_cuft

    cured_bound_water = scaled_cm_wt * bound_water_factor
    cured_dry_wt = (base_dry_wt * scale) + cured_bound_water + (adm_solids_wt * scale)
    cured_dry_density = cured_dry_wt / target_volume_cuft

    oven_dry_wt = (base_dry_wt * scale) + (adm_solids_wt * scale)
    oven_dry_density = oven_dry_wt / target_volume_cuft

    # 7. Strength Prediction Model
    base_strength = 16000.0 / (6.5 ** w_cm)
    tot_cm_pct = sum(m["pct"] for m in dry_materials if m["is_cm"])
    k_cm = sum(m["pct"] * m["efficiency"] for m in dry_materials if m["is_cm"]) / (tot_cm_pct if tot_cm_pct > 0 else 1.0)
    k_density = (cured_dry_density / 145.0) ** 0.82

    active_d50s = sorted([m["d50_microns"] for m in dry_materials if m["pct"] > 0.1 and m["d50_microns"] > 0])
    span = (active_d50s[-1] / active_d50s[0]) if active_d50s else 1.0
    k_packing = min(1.15, 0.90 + (0.04 * math.log10(span)))
    k_air = math.exp(-2.5 * (air_pct / 100.0))
    polymer_cm_ratio = (adm_solids_wt * scale) / (scaled_cm_wt if scaled_cm_wt > 0 else 1.0)
    k_polymer = 1.0 + (0.50 * polymer_cm_ratio)

    predicted_psi = base_strength * k_cm * k_density * k_packing * k_air * k_polymer * cal_factor
    predicted_mpa = predicted_psi * 0.00689476

    return {
        "target_vol": target_volume_cuft,
        "w_cm": w_cm,
        "air_pct": air_pct,
        "wet_density": wet_density,
        "cured_dry_density": cured_dry_density,
        "oven_dry_density": oven_dry_density,
        "predicted_psi": predicted_psi,
        "predicted_mpa": predicted_mpa,
        "scaled_dry": scaled_dry,
        "total_cm_wt": scaled_cm_wt,
        "net_water_wt": net_added_water,
        "scaled_admixtures": scaled_admixtures
    }

def print_report(results):
    print("=" * 65)
    print("             CONCRETE MIX DESIGN & BATCH REPORT        ")
    print("=" * 65)
    print(f" Target Batch Volume  : {results['target_vol']:.2f} cu ft")
    print(f" Water/Cement Ratio   : {results['w_cm']:.2f}")
    print(f" Target Air Content   : {results['air_pct']:.1f} %")
    print("-" * 65)
    print(" PERFORMANCE ESTIMATES:")
    print(f"  * Wet Density       : {results['wet_density']:.2f} lb/ft^3")
    print(f"  * Cured Dry Density : {results['cured_dry_density']:.2f} lb/ft^3")
    print(f"  * Oven-Dried Density: {results['oven_dry_density']:.2f} lb/ft^3")
    print(f"  * 28-Day Strength   : {results['predicted_psi']:.0f} PSI ({results['predicted_mpa']:.1f} MPa)")
    print("-" * 65)
    print(" BATCHING WEIGHTS:")
    print("  [ Dry Materials ]")
    for m in results["scaled_dry"]:
        cm_flag = " (CM)" if m["is_cm"] else ""
        print(f"   - {m['name']:<26} : {m['wt']:6.2f} lbs  ({m['pct']:5.2f}%){cm_flag}")
    print("   -------------------------------------------------")
    print(f"   Total Cementitious (CM) Wt : {results['total_cm_wt']:6.2f} lbs")
    print("\n  [ Liquids & Admixtures ]")
    print(f"   - Net Mixing Water         : {results['net_water_wt']:6.2f} lbs")
    for a in results["scaled_admixtures"]:
        print(f"   - {a['name']:<26} : {a['wt']:6.2f} lbs  ({a['dose_cwt']} oz/cwt)")
    print("=" * 65)

if __name__ == "__main__":
    target_vol = 1.0
    if len(sys.argv) > 1:
        try:
            target_vol = float(sys.argv[1])
        except ValueError:
            pass

    db = load_json("ingredients.json")
    mix = load_json("mix.json")
    results = calculate_mix(db, mix, target_volume_cuft=target_vol)
    print_report(results)
     
    