
    if query == "Nike Shox":
        if "nike" not in text or "shox" not in title:
            return False

        clothing_words = [
            "kurtka", "kurtkę", "kurtki",
            "bluza", "bluzy", "hoodie", "sweatshirt",
            "koszulka", "koszulki", "t shirt",
            "spodenki", "spodnie", "czapka", "czapki",
            "jacket", "shirt", "shorts", "trousers",
            "wiatrówka", "wiatrowka", "socks",
            "skarpetki", "tee"
        ]

        if any(word in title for word in clothing_words):
            return False

        shoe_models = [
            "shox tl", "shox r4", "shox ride",
            "shox rivalry", "shox monster",
            "shox turbo", "shox gravity",
            "shox enigma", "shox nz",
            "shox tlx", "shox bb4", "shox r2",
            "shox vc", "shox xt"
        ]

        shoe_words = [
            "buty", "sneakers", "sneaker", "shoes",
            "obuwie", "tenisówki", "tenisky",
            "trainers", "trampki", "sneakersy"
        ]

        if any(model in title for model in shoe_models):
            return True

        if any(word in title for word in shoe_words):
            return True

        if title.strip() in ("nike shox", "nike shox nike"):
            return True

        return False
