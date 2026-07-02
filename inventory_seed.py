"""Default demo inventory — seeded into Postgres or JSON on first run."""

from __future__ import annotations

from typing import Any

DEFAULT_INVENTORY: list[dict[str, Any]] = [
    {
        "id": "patek-nautilus-5980-1400g",
        "brand": "Patek Philippe",
        "model": "Nautilus Chronograph",
        "title": "Nautilus Ref. 5980/1400G",
        "subtitle": "White Gold · Full factory diamond setting · 41mm",
        "reference": "5980/1400G",
        "case_material": "White Gold",
        "dial": "Black",
        "case_size_mm": 41,
        "stones": "Diamonds",
        "price_display": "$640,575",
        "price_usd": 640575,
        "in_stock": True,
        "status": "available",
        "image_url": (
            "https://timepiecetradingllc.com/cdn/shop/files/"
            "1_d4effa35-729b-4050-970a-ccce1e5130e9.png?v=1751475739&width=700"
        ),
        "product_url": (
            "https://timepiecetradingllc.com/products/"
            "5980-1400g-white-gold-factory-set-nautilus-2023"
        ),
        "inquire_url": (
            "https://timepiecetradingllc.com/products/"
            "5980-1400g-white-gold-factory-set-nautilus-2023"
        ),
    },
    {
        "id": "rm67-02-sebastien-ogier",
        "brand": "Richard Mille",
        "model": "RM 67-02 Sebastien Ogier",
        "title": "RM67-02 Sebastien Ogier",
        "subtitle": "Black Carbon NTPT · Elastic strap · 50mm",
        "reference": "RM 67-02",
        "case_material": "NTPT Carbon",
        "dial": "Openworked",
        "case_size_mm": 50,
        "stones": "No Diamonds",
        "price_display": "$460,000",
        "price_usd": 460000,
        "in_stock": True,
        "status": "available",
        "image_url": (
            "https://timepiecetradingllc.com/cdn/shop/files/"
            "1_aa692060-129b-4441-9fa1-53d53edc9f54.png?v=1770834542&width=700"
        ),
        "product_url": (
            "https://timepiecetradingllc.com/products/"
            "rm67-02-black-ntpt-sebastian-olgier-2022-complete-with-box-and-papers"
        ),
        "inquire_url": (
            "https://timepiecetradingllc.com/products/"
            "rm67-02-black-ntpt-sebastian-olgier-2022-complete-with-box-and-papers"
        ),
    },
    {
        "id": "rm055-bubba-watson",
        "brand": "Richard Mille",
        "model": "RM 055 Bubba Watson",
        "title": "RM055 White Ceramic Bubba Watson",
        "subtitle": "Rubberized Titanium Case · Rubber strap",
        "reference": "RM 055",
        "case_material": "Ceramic",
        "dial": "Openworked",
        "case_size_mm": None,
        "stones": "No Diamonds",
        "price_display": "$369,000",
        "price_usd": 369000,
        "in_stock": False,
        "status": "sold_out",
        "image_url": (
            "https://timepiecetradingllc.com/cdn/shop/files/"
            "12_c1808893-7641-479c-a823-6b4b79438eb3.png?v=1761235300&width=700"
        ),
        "product_url": (
            "https://timepiecetradingllc.com/collections/entire-collection/products/"
            "richard-mille-rm055-white-ceramic-bubba-watson"
        ),
        "inquire_url": (
            "https://timepiecetradingllc.com/collections/entire-collection/products/"
            "richard-mille-rm055-white-ceramic-bubba-watson"
        ),
    },
    {
        "id": "patek-aquanaut-5167r",
        "brand": "Patek Philippe",
        "model": "Aquanaut",
        "title": "Aquanaut Ref. 5167R-001",
        "subtitle": "Rose Gold · Chocolate dial · 41mm",
        "reference": "5167R-001",
        "case_material": "Rose Gold",
        "dial": "Brown",
        "case_size_mm": 41,
        "stones": "No Diamonds",
        "price_display": "$127,000",
        "price_usd": 127000,
        "in_stock": True,
        "status": "available",
        "image_url": (
            "https://timepiecetradingllc.com/cdn/shop/files/"
            "Untitled_40_36_ba64318e-c419-4230-a13d-e71bc62a2783.png?v=1744792582&width=700"
        ),
        "product_url": (
            "https://timepiecetradingllc.com/products/"
            "5167r-001-aquanaut-40-8mm-chocolate-brown-tropical-strap-brown-embossed-dial-rose-gold-bezel"
        ),
        "inquire_url": (
            "https://timepiecetradingllc.com/products/"
            "5167r-001-aquanaut-40-8mm-chocolate-brown-tropical-strap-brown-embossed-dial-rose-gold-bezel"
        ),
    },
    {
        "id": "rolex-day-date-228235-olive",
        "brand": "Rolex",
        "model": "Day-Date 40",
        "title": "Day-Date 228235 Olive Dial",
        "subtitle": "Everose Gold · President bracelet · Fluted bezel · 40mm",
        "reference": "228235",
        "case_material": "Rose Gold",
        "dial": "Green",
        "case_size_mm": 40,
        "stones": "No Diamonds",
        "price_display": "$64,500",
        "price_usd": 64500,
        "in_stock": False,
        "status": "sold_out",
        "image_url": (
            "https://timepiecetradingllc.com/cdn/shop/files/"
            "Untitleddesign_11_6e4c6365-cdec-478a-a366-4925e41fda80.png?v=1749230626&width=700"
        ),
        "product_url": (
            "https://timepiecetradingllc.com/products/"
            "rolex-day-date-228235-40mm-rose-gold-olive-dial"
        ),
        "inquire_url": (
            "https://timepiecetradingllc.com/products/"
            "rolex-day-date-228235-40mm-rose-gold-olive-dial"
        ),
    },
]
