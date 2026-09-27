// Interactor de "Adivina el número" con testlib, como en Codeforces/Polygon.
//
// No hay que subir testlib.h: el sitio lo detecta por el #include y lo pone él.
//   inf  = entrada del caso (n y el número secreto)
//   ouf  = lo que imprime el participante
//   cout = lo que le llega al participante (vaciar con endl)
// quitf(_ok, ...) = Aceptado; quitf(_wa, ...) = Respuesta incorrecta.
// El texto de quitf sólo lo ve el participante si se marca
// «mostrar mensajes del interactor»; no pongas ahí la respuesta.
#include "testlib.h"
#include <iostream>
#include <string>
using namespace std;

const int MAX_PREGUNTAS = 30;

int main(int argc, char *argv[]) {
    registerInteraction(argc, argv);
    long long n = inf.readLong();
    long long secreto = inf.readLong();

    cout << n << endl;

    int preguntas = 0;
    while (true) {
        string tipo = ouf.readToken("[?!]", "tipo");
        long long x = ouf.readLong(1, n, "x");
        if (tipo == "!") {
            if (x == secreto) quitf(_ok, "adivinó con %d preguntas", preguntas);
            quitf(_wa, "respuesta incorrecta");
        }
        if (++preguntas > MAX_PREGUNTAS) {
            cout << -1 << endl;
            quitf(_wa, "más de %d preguntas", MAX_PREGUNTAS);
        }
        cout << (secreto > x ? ">" : secreto < x ? "<" : "=") << endl;
    }
}
