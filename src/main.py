import pandas as pd


def main():
    df = pd.read_parquet(
        "data/raw/cancer-myth/validation-00000-of-00001.parquet"
    )

    print(df.head())
    print()
    print(f"Rows: {len(df)}")
    print(f"Columns: {list(df.columns)}")


if __name__ == "__main__":
    main()