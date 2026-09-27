# Interactor de "Adivina el número", versión en Python.
#
# El juez lo ejecuta así:   interactor.py <caso.in> <caso.out>
# print(..., flush=True) le habla al participante; sys.stdin.readline() lo
# escucha. sys.exit(0) = Aceptado, sys.exit(1) = Respuesta incorrecta.
# Lo que vaya a sys.stderr sólo lo ve el participante si se marca
# «mostrar mensajes del interactor».
import sys

MAX_PREGUNTAS = 30


def rechazar(motivo):
    print(motivo, file=sys.stderr)
    sys.exit(1)


def main():
    with open(sys.argv[1]) as caso:
        n, secreto = map(int, caso.read().split())

    print(n, flush=True)

    preguntas = 0
    while True:
        linea = sys.stdin.readline()
        if not linea:
            rechazar('El programa terminó sin dar su respuesta')
        partes = linea.split()
        if len(partes) != 2 or partes[0] not in ('?', '!'):
            rechazar('Se esperaba ? x o ! x')
        try:
            x = int(partes[1])
        except ValueError:
            rechazar('x no es un entero')

        if partes[0] == '!':
            if x == secreto:
                sys.exit(0)
            rechazar('Respuesta incorrecta')

        preguntas += 1
        if preguntas > MAX_PREGUNTAS:
            print(-1, flush=True)
            rechazar('Más de %d preguntas' % MAX_PREGUNTAS)
        print('>' if secreto > x else '<' if secreto < x else '=', flush=True)


main()
