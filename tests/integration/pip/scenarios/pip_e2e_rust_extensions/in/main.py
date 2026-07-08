from cryptography.fernet import Fernet


def main() -> None:
    Fernet.generate_key()
    print("Hello, world!")


if __name__ == "__main__":
    main()
