from app.database import Base, SessionLocal, engine
from app.models import MaterialInventory


SAMPLE_INVENTORY = [
    {
        "material_code": "SCRAP-A",
        "inventory_type": "scrap",
        "material": "50#",
        "thickness": 2.65,
        "shape": "circle",
        "diameter": 130,
        "usable_size": "φ130 × 2.65",
        "quantity": 1,
        "location": "A-01",
        "status": "available",
    },
    {
        "material_code": "SCRAP-B",
        "inventory_type": "scrap",
        "material": "50#",
        "thickness": 2.65,
        "shape": "circle",
        "diameter": 180,
        "usable_size": "φ180 × 2.65",
        "quantity": 1,
        "location": "A-02",
        "status": "available",
    },
]


def main() -> None:
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        for item in SAMPLE_INVENTORY:
            exists = db.query(MaterialInventory).filter(MaterialInventory.material_code == item["material_code"]).first()
            if not exists:
                db.add(MaterialInventory(**item))
        db.commit()
    finally:
        db.close()


if __name__ == "__main__":
    main()
