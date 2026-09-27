// Interactor de "Adivina el número", versión simple (sin testlib).
//
// El juez lo ejecuta así:   interactor <caso.in> <caso.out>
//   - argv[1] es la entrada del caso: aquí, n y el número secreto.
//   - argv[2] es la salida esperada del caso. Este interactor no la usa.
//
// Lo que se imprime con cout le llega al participante; lo que el participante
// imprime se lee con cin. Hay que vaciar el búfer tras cada mensaje (endl).
//
// Veredicto: return 0 = Aceptado, return 1 = Respuesta incorrecta.
// Lo que se escriba en cerr sólo lo ve el participante si se marca
// «mostrar mensajes del interactor»; no escribas ahí la respuesta.
#include <fstream>
#include <iostream>
#include <string>
using namespace std;

const int MAX_PREGUNTAS = 30;

int main(int argc, char *argv[]) {
    ifstream caso(argv[1]);
    long long n, secreto;
    caso >> n >> secreto;

    cout << n << endl;

    int preguntas = 0;
    string tipo;
    long long x;
    while (cin >> tipo >> x) {
        if (tipo == "!") {
            if (x == secreto) return 0;
            cerr << "Respuesta incorrecta" << endl;
            return 1;
        }
        if (tipo != "?") {
            cerr << "Se esperaba ? o !" << endl;
            return 1;
        }
        if (++preguntas > MAX_PREGUNTAS) {
            // -1 avisa al participante que termine. Si sale solo, el veredicto
            // es Respuesta incorrecta; si sigue leyendo, puede salir otro.
            cout << -1 << endl;
            cerr << "Más de " << MAX_PREGUNTAS << " preguntas" << endl;
            return 1;
        }
        cout << (secreto > x ? ">" : secreto < x ? "<" : "=") << endl;
    }
    cerr << "El programa terminó sin dar su respuesta" << endl;
    return 1;
}
