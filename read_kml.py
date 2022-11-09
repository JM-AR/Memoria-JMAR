import numpy as np
import pandas as pd
import geopandas as gpd
import fiona


def complete_kml(df, frames):
    short_df = df.loc[:, ('Name', 'Latitude', 'Longitude')]  # Se saca la info importante
    short_df['Name'] = short_df['Name'].astype(int)  # Se pasa de str a int
    prev_stop = 0  #
    new_df = short_df.iloc[:0]  # DF base
    for i in range(1, len(short_df) - 1):  # Se obvia el índice 0 porque ese está bien el 100% de las veces

        if short_df.iloc[i]['Name'] != (short_df.iloc[i + 1]['Name'] - 1):  # en caso de que haya un salto de valores

            line = short_df.iloc[i:i + 1]  # Se obtiene la información de la fila i como DF
            replace_val = line.iat[0, 0]  # Valor a reemplazar de la linea
            dist = short_df.at[i + 1, 'Name'] - replace_val - 1  # Valores a rellenar
            correct_rows = short_df.iloc[prev_stop:i + 1]  # se debe sacar la fila i, por eso se le suma 1
            prev_stop = i + 1

            new_df = pd.concat([new_df, correct_rows]).reset_index(
                drop=True)  # Se rellenan las filas que ya están ordenadas

            for j in range(1, dist + 1):
                new_line = line.replace({'Name': replace_val},
                                        replace_val + j)  # Se cambia el valor del nombre del punto

                new_df = pd.concat([new_df, new_line]).reset_index(
                    drop=True)  # Se agrega al df para rellenar la linealidad

    correct_rows = short_df.iloc[prev_stop:]
    new_df = pd.concat([new_df, correct_rows]).reset_index(drop=True)

    if len(new_df) < frames:  # caso en que se quede quieto en los últimos frames
        line = short_df.iloc[len(short_df) - 1: len(short_df)]  # última fila del DF original
        replace_val = line.iat[0, 0]
        dist = frames - replace_val  # no hay dato siguiente, así que la distancia es completa

        for j in range(1, dist + 1):
            new_line = line.replace({'Name': replace_val}, replace_val + j)  # Se cambia el valor del nombre del punto
            new_df = pd.concat([new_df, new_line]).reset_index(drop=True)  # Se agrega al df para rellenar la linealidad

    return new_df
