// Solución de referencia: búsqueda binaria. Cada pregunta termina en endl,
// que vacía el búfer; sin eso el interactor nunca la recibe.
#include <iostream>
#include <string>
using namespace std;

int main() {
    long long n;
    cin >> n;
    long long lo = 1, hi = n;
    while (lo < hi) {
        long long mid = lo + (hi - lo) / 2;
        cout << "? " << mid << endl;
        string r;
        cin >> r;
        if (r == "-1") return 0;
        if (r == "=") { lo = hi = mid; break; }
        if (r == ">") lo = mid + 1; else hi = mid - 1;
    }
    cout << "! " << lo << endl;
}
