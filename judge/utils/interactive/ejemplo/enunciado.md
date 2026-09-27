El juez eligió un número secreto entre 1 y n. Encuéntralo haciendo como máximo 30 preguntas.

## Interacción

Este es un problema interactivo: tu programa conversa con el juez en lugar de leer un archivo.

Primero lee una línea con el entero n (1 ≤ n ≤ 10^9).

Para preguntar, imprime una línea `? x` con 1 ≤ x ≤ n. El juez responde con una línea:

- `>` si el número secreto es mayor que x;
- `<` si es menor;
- `=` si es igual.

Cuando lo sepas, imprime `! x` y termina. Esa línea no cuenta como pregunta.

Si haces más de 30 preguntas, el juez responde `-1`: termina tu programa en cuanto lo leas.

**Después de cada línea que imprimas, vacía el búfer de salida**, o el juez no la recibirá y tu programa excederá el tiempo:

- C++: `cout << ... << endl;` o `fflush(stdout);` tras `printf`
- Python: `print(..., flush=True)`
- Java: `System.out.flush();`

## Ejemplo

| Tu programa | El juez |
|---|---|
| | `100` |
| `? 50` | |
| | `<` |
| `? 25` | |
| | `>` |
| `? 37` | |
| | `=` |
| `! 37` | |
