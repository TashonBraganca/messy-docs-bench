"""Generate synthetic W-2s from the official IRS form, with an exact answer key.

SSNs use the 900-999 area, which the SSA never issues, so no real person's number can appear.
Each form is rendered, then damaged like a phone scan (tilt, blur, noise, JPEG).
Usage: python3 make_tax.py 20
"""
import json, random, sys
from pathlib import Path
import pymupdf
from faker import Faker
from PIL import Image, ImageFilter

HERE = Path(__file__).parent
OUT = HERE / "docs" / "w2"
fake = Faker("en_US")

# Field ids on the top form of page 6 (Copy C), confirmed by rendering the ids onto the page.
FIELDS = {"f2_01": "employee_ssn", "f2_02": "employer_ein", "f2_03": "employer_name_address",
          "f2_05": "employee_first_name", "f2_06": "employee_last_name", "f2_08": "employee_address",
          "f2_09": "box1_wages", "f2_10": "box2_federal_tax", "f2_11": "box3_ss_wages",
          "f2_12": "box4_ss_tax", "f2_13": "box5_medicare_wages", "f2_14": "box6_medicare_tax",
          "f2_20": "box12a_code", "f2_21": "box12a_amount", "f2_31": "box15_state",
          "f2_35": "box16_state_wages", "f2_37": "box17_state_tax"}
SS_WAGE_BASE = 184500  # assumption for tax year 2026; only affects realism of box 3


def money(x):
    return f"{x:,.2f}"


def one_record():
    wages = round(random.uniform(18000, 260000), 2)
    ss_wages = min(wages, SS_WAGE_BASE)
    state = random.choice(["CA", "NY", "TX", "IL", "NJ", "GA", "WA", "MA"])
    no_state_tax = state in ("TX", "WA")
    k401 = round(wages * random.uniform(0.02, 0.1), 2)
    return {
        "employee_ssn": f"{random.randint(900, 999)}-{random.randint(10, 99)}-{random.randint(1000, 9999)}",
        "employer_ein": f"{random.randint(10, 99)}-{random.randint(1000000, 9999999)}",
        "employer_name_address": f"{fake.company()}\n{fake.street_address()}\n{fake.city()}, {state} {fake.zipcode()}",
        "employee_first_name": fake.first_name(),
        "employee_last_name": fake.last_name(),
        "employee_address": f"{fake.street_address()}\n{fake.city()}, {state} {fake.zipcode()}",
        "box1_wages": money(wages - k401),
        "box2_federal_tax": money(wages * random.uniform(0.08, 0.24)),
        "box3_ss_wages": money(ss_wages),
        "box4_ss_tax": money(ss_wages * 0.062),
        "box5_medicare_wages": money(wages),
        "box6_medicare_tax": money(wages * 0.0145),
        "box12a_code": "D",
        "box12a_amount": money(k401),
        "box15_state": state,
        "box16_state_wages": "" if no_state_tax else money(wages - k401),
        "box17_state_tax": "" if no_state_tax else money(wages * random.uniform(0.02, 0.08)),
    }


def damage(img, level):
    img = img.convert("L").rotate(random.uniform(-2.5, 2.5) * level, expand=True, fillcolor=255)
    if level > 1.2:  # "extreme": low-resolution phone photo with uneven light
        w, h = img.size
        img = img.resize((int(w * 0.72), int(h * 0.72)))
        shade = Image.linear_gradient("L").resize(img.size).point(lambda v: 255 - v // 3)
        img = Image.composite(img, shade, Image.new("L", img.size, 170))
    img = img.filter(ImageFilter.GaussianBlur(0.7 if level > 1.2 else 0.4 + 0.8 * level))
    px = img.load()
    w, h = img.size
    for _ in range(int(w * h * 0.01 * min(level, 1.0))):
        px[random.randrange(w), random.randrange(h)] = random.randint(0, 255)
    return img


def main(n):
    OUT.mkdir(parents=True, exist_ok=True)
    random.seed(7); Faker.seed(7)
    for i in range(n):
        rec = one_record()
        doc = pymupdf.open(HERE / "data" / "fw2.pdf")
        doc.select([5])
        page = doc[0]
        for wd in page.widgets():
            key = wd.field_name.split(".")[-1].replace("[0]", "")
            if wd.field_type == pymupdf.PDF_WIDGET_TYPE_TEXT and key in FIELDS and "Copy1" not in wd.field_name:
                wd.field_value = rec[FIELDS[key]]
                wd.update()
        pix = page.get_pixmap(dpi=150, clip=pymupdf.Rect(0, 0, page.rect.width, page.rect.height / 2))
        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        level = [0.3, 0.7, 1.0, 1.6][i % 4]  # light, medium, heavy, extreme damage in rotation
        name = f"w2_{i:03d}"
        damage(img, level).save(OUT / f"{name}.jpg", quality=random.randint(30, 45) if level > 1.2 else random.randint(45, 75))
        (OUT / f"{name}.json").write_text(json.dumps({"doc_type": "w2", "damage": level, "truth": rec}, indent=1))
    print(f"wrote {n} W-2s to {OUT}")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 20)
