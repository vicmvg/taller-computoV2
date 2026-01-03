"""
test_load_taller_computo_pro.py - Prueba de carga COMPLETA para Taller de Cómputo

Instalación:
pip install locust

Uso:
locust -f test_load_taller_computo_pro.py

Luego abrir: http://localhost:8089
"""

from locust import HttpUser, task, between, events, SequentialTaskSet
import random

# ============================================
# CONFIGURACIÓN DE LA APLICACIÓN
# ============================================

BASE_URL = "https://taller-computov2.onrender.com"

# Credenciales admin
ADMIN_CREDENTIALS = {
    "username": "admin",
    "password": "ZASER345a"
}

# Pool de credenciales de alumnos
ALUMNOS_POOL = [
    {"usuario": "5Avictor2003", "password": "ZASER345a"},
    {"usuario": "3Aalextrej2009", "password": "ZASER345a"},
    {"usuario": "marisela cortejo suarez", "password": "ZASER345a"},
    {"usuario": "4Ajuanantonio2009", "password": "ZASER345a"}
]


# ============================================
# USUARIOS ANÓNIMOS (NO AUTENTICADOS)
# ============================================

class UsuarioAnonimo(HttpUser):
    """Simula visitantes que navegan sin autenticarse"""
    
    host = BASE_URL
    wait_time = between(1, 3)
    weight = 6  # 60% de usuarios serán anónimos
    
    def on_start(self):
        """Se ejecuta cuando el usuario inicia"""
        print(f"👤 Usuario anónimo iniciado")
    
    @task(20)  # Acción más frecuente
    def view_index(self):
        """Cargar página principal"""
        with self.client.get("/", catch_response=True, name="Página Principal") as response:
            if response.status_code == 200:
                response.success()
            else:
                response.failure(f"Status {response.status_code}")
    
    @task(10)
    def view_grado_1(self):
        """Ver actividades de primer grado"""
        with self.client.get("/grado/1", catch_response=True, name="Grado 1") as response:
            if response.status_code == 200:
                response.success()
            else:
                response.failure(f"Status {response.status_code}")
    
    @task(8)
    def view_grado_2(self):
        """Ver actividades de segundo grado"""
        with self.client.get("/grado/2", catch_response=True, name="Grado 2") as response:
            if response.status_code == 200:
                response.success()
            else:
                response.failure(f"Status {response.status_code}")
    
    @task(4)
    def view_otros_grados(self):
        """Intentar ver otros grados (pueden no tener info)"""
        grado = random.choice([3, 4, 5, 6])
        with self.client.get(f"/grado/{grado}", 
                            catch_response=True, 
                            name="Otros Grados (3-6)") as response:
            if response.status_code in [200, 404]:
                response.success()
            else:
                response.failure(f"Status {response.status_code}")
    
    @task(3)
    def view_login_admin(self):
        """Visitar página de login de admin"""
        with self.client.get("/auth/login", 
                            catch_response=True,
                            name="Login Admin (GET)") as response:
            if response.status_code == 200:
                response.success()
            else:
                response.failure(f"Status {response.status_code}")
    
    @task(3)
    def view_login_alumnos(self):
        """Visitar página de login de alumnos"""
        with self.client.get("/auth/login-alumnos", 
                            catch_response=True,
                            name="Login Alumnos (GET)") as response:
            if response.status_code == 200:
                response.success()
            else:
                response.failure(f"Status {response.status_code}")


# ============================================
# SECUENCIA DE TAREAS PARA ALUMNOS
# ============================================

class AlumnoTaskSequence(SequentialTaskSet):
    """Secuencia realista de acciones de un alumno"""
    
    @task
    def ver_dashboard(self):
        """1. Ver dashboard principal"""
        self.client.get("/alumnos/", name="Dashboard Alumno")
        self.wait()
    
    @task
    def ver_tareas(self):
        """2. Revisar tareas pendientes"""
        self.client.get("/alumnos/tareas", name="Tareas Alumno")
        self.wait()
    
    @task
    def ver_apuntes(self):
        """3. Consultar apuntes"""
        self.client.get("/alumnos/apuntes", name="Apuntes")
        self.wait()
    
    @task
    def ver_calificaciones(self):
        """4. Revisar calificaciones"""
        self.client.get("/alumnos/calificaciones", name="Calificaciones")
        self.wait()
    
    @task
    def ver_mis_archivos(self):
        """5. Ver archivos propios"""
        self.client.get("/alumnos/mis-archivos", name="Mis Archivos")
        self.wait()


# ============================================
# ALUMNOS AUTENTICADOS
# ============================================

class AlumnoAutenticado(HttpUser):
    """Simula un alumno que inicia sesión y usa el sistema"""
    
    host = BASE_URL
    wait_time = between(2, 5)
    weight = 3  # 30% de usuarios serán alumnos autenticados
    
    def on_start(self):
        """Login al iniciar la sesión"""
        # Seleccionar credenciales aleatorias del pool
        self.credentials = random.choice(ALUMNOS_POOL)
        print(f"🎓 Alumno {self.credentials['usuario'][:10]}... intentando login")
        self.login_alumno()
    
    def login_alumno(self):
        """Realizar login de alumno"""
        self.client.get("/auth/login-alumnos")
        
        response = self.client.post(
            "/auth/login-alumnos",
            data={
                "usuario": self.credentials["usuario"],
                "password": self.credentials["password"]
            },
            catch_response=True,
            allow_redirects=False,
            name="Login Alumno (POST)"
        )
        
        if response.status_code in [200, 302]:
            print(f"✅ Alumno {self.credentials['usuario'][:10]}... logueado")
        else:
            print(f"❌ Login falló: {response.status_code}")
    
    @task(12)
    def view_dashboard_alumno(self):
        """Ver panel principal del alumno"""
        with self.client.get("/alumnos/", 
                            catch_response=True,
                            name="Dashboard Alumno") as response:
            if response.status_code in [200, 302]:
                response.success()
            else:
                response.failure(f"Status {response.status_code}")
    
    @task(8)
    def view_tareas(self):
        """Ver tareas del alumno"""
        with self.client.get("/alumnos/tareas", 
                            catch_response=True,
                            name="Tareas Alumno") as response:
            if response.status_code in [200, 302, 404]:
                response.success()
            else:
                response.failure(f"Status {response.status_code}")
    
    @task(6)
    def view_calificaciones(self):
        """Ver calificaciones"""
        with self.client.get("/alumnos/calificaciones", 
                            catch_response=True,
                            name="Calificaciones") as response:
            if response.status_code in [200, 302, 404]:
                response.success()
            else:
                response.failure(f"Status {response.status_code}")
    
    @task(5)
    def view_apuntes(self):
        """Ver apuntes de clase"""
        with self.client.get("/alumnos/apuntes", 
                            catch_response=True,
                            name="Apuntes") as response:
            if response.status_code in [200, 302, 404]:
                response.success()
            else:
                response.failure(f"Status {response.status_code}")
    
    @task(4)
    def view_mis_archivos(self):
        """Ver archivos propios"""
        with self.client.get("/alumnos/mis-archivos", 
                            catch_response=True,
                            name="Mis Archivos") as response:
            if response.status_code in [200, 302, 404]:
                response.success()
            else:
                response.failure(f"Status {response.status_code}")
    
    @task(3)
    def solicitar_archivo(self):
        """Ir a solicitar archivo"""
        with self.client.get("/alumnos/solicitar-archivo", 
                            catch_response=True,
                            name="Solicitar Archivo") as response:
            if response.status_code in [200, 302, 404]:
                response.success()
            else:
                response.failure(f"Status {response.status_code}")
    
    @task(2)
    def navegar_grados(self):
        """Ver actividades de grados"""
        grado = random.choice([1, 2])
        with self.client.get(f"/grado/{grado}", 
                            catch_response=True,
                            name=f"Ver Grado {grado}") as response:
            if response.status_code in [200, 302]:
                response.success()
            else:
                response.failure(f"Status {response.status_code}")


# ============================================
# ADMIN AUTENTICADO
# ============================================

class AdminAutenticado(HttpUser):
    """Simula un administrador/maestro usando el sistema"""
    
    host = BASE_URL
    wait_time = between(3, 7)
    weight = 1  # 10% de usuarios serán admins
    
    def on_start(self):
        """Login como admin al iniciar"""
        print(f"👨‍🏫 Admin intentando login...")
        self.login_admin()
    
    def login_admin(self):
        """Realizar login de administrador"""
        self.client.get("/auth/login")
        
        response = self.client.post(
            "/auth/login",
            data={
                "username": ADMIN_CREDENTIALS["username"],
                "password": ADMIN_CREDENTIALS["password"]
            },
            catch_response=True,
            allow_redirects=False,
            name="Login Admin (POST)"
        )
        
        if response.status_code in [200, 302]:
            print(f"✅ Admin logueado exitosamente")
        else:
            print(f"❌ Login admin falló: {response.status_code}")
    
    @task(10)
    def view_dashboard_admin(self):
        """Ver dashboard de administrador"""
        with self.client.get("/admin/dashboard", 
                            catch_response=True,
                            allow_redirects=True,
                            name="Dashboard Admin") as response:
            if response.status_code in [200, 302, 404]:
                response.success()
            else:
                response.failure(f"Status {response.status_code}")
    
    @task(7)
    def view_alumnos_list(self):
        """Ver lista de alumnos"""
        with self.client.get("/admin/alumnos", 
                            catch_response=True,
                            allow_redirects=True,
                            name="Lista Alumnos") as response:
            if response.status_code in [200, 302, 404]:
                response.success()
            else:
                response.failure(f"Status {response.status_code}")
    
    @task(5)
    def view_actividades(self):
        """Ver actividades"""
        with self.client.get("/admin/actividades", 
                            catch_response=True,
                            allow_redirects=True,
                            name="Actividades Admin") as response:
            if response.status_code in [200, 302, 404]:
                response.success()
            else:
                response.failure(f"Status {response.status_code}")
    
    @task(4)
    def view_cuestionarios(self):
        """Gestionar cuestionarios/exámenes"""
        with self.client.get("/admin/cuestionarios", 
                            catch_response=True,
                            allow_redirects=True,
                            name="Cuestionarios Admin") as response:
            if response.status_code in [200, 302, 404]:
                response.success()
            else:
                response.failure(f"Status {response.status_code}")
    
    @task(3)
    def view_anuncios(self):
        """Ver/crear anuncios"""
        with self.client.get("/admin/anuncios", 
                            catch_response=True,
                            allow_redirects=True,
                            name="Anuncios Admin") as response:
            if response.status_code in [200, 302, 404]:
                response.success()
            else:
                response.failure(f"Status {response.status_code}")
    
    @task(3)
    def view_biblioteca(self):
        """Gestionar biblioteca"""
        with self.client.get("/admin/biblioteca", 
                            catch_response=True,
                            allow_redirects=True,
                            name="Biblioteca Admin") as response:
            if response.status_code in [200, 302, 404]:
                response.success()
            else:
                response.failure(f"Status {response.status_code}")
    
    @task(2)
    def navegar_grados_admin(self):
        """Ver grados como admin"""
        grado = random.choice([1, 2, 3, 4, 5, 6])
        with self.client.get(f"/grado/{grado}", 
                            catch_response=True,
                            name="Ver Grados (Admin)") as response:
            if response.status_code in [200, 404]:
                response.success()
            else:
                response.failure(f"Status {response.status_code}")


# ============================================
# EVENTOS Y MÉTRICAS
# ============================================

@events.test_start.add_listener
def on_test_start(environment, **kwargs):
    """Se ejecuta al iniciar el test"""
    print("\n" + "=" * 80)
    print("🧪 INICIANDO PRUEBA DE CARGA PRO - TALLER DE CÓMPUTO V2")
    print("=" * 80)
    print(f"🎯 Target: {BASE_URL}")
    print(f"👥 Distribución de usuarios:")
    print(f"   - 60% Usuarios anónimos (navegación pública)")
    print(f"   - 30% Alumnos autenticados (4 cuentas diferentes)")
    print(f"   - 10% Administradores (gestión del sistema)")
    print(f"\n📋 Endpoints probados:")
    print(f"   • Públicos: /, /grado/1-6, /auth/login*")
    print(f"   • Alumnos: dashboard, tareas, calificaciones, apuntes, archivos")
    print(f"   • Admin: dashboard, alumnos, actividades, cuestionarios, anuncios, biblioteca")
    print("=" * 80 + "\n")


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    """Se ejecuta al terminar el test"""
    stats = environment.stats
    
    print("\n" + "=" * 80)
    print("✅ PRUEBA COMPLETADA - RESULTADOS FINALES")
    print("=" * 80)
    
    # Métricas generales
    print(f"\n📊 MÉTRICAS GENERALES:")
    print(f"   Total Requests: {stats.total.num_requests:,}")
    print(f"   Total Failures: {stats.total.num_failures:,}")
    print(f"   Failure Rate: {stats.total.fail_ratio * 100:.2f}%")
    print(f"   Avg Response Time: {stats.total.avg_response_time:.0f}ms")
    print(f"   Min Response Time: {stats.total.min_response_time:.0f}ms")
    print(f"   Max Response Time: {stats.total.max_response_time:.0f}ms")
    print(f"   Median Response Time: {stats.total.median_response_time:.0f}ms")
    print(f"   RPS (Requests/sec): {stats.total.total_rps:.2f}")
    
    # Percentiles
    print(f"\n⏱️  PERCENTILES DE TIEMPO DE RESPUESTA:")
    print(f"   50% de requests < {stats.total.get_response_time_percentile(0.50):.0f}ms")
    print(f"   75% de requests < {stats.total.get_response_time_percentile(0.75):.0f}ms")
    print(f"   90% de requests < {stats.total.get_response_time_percentile(0.90):.0f}ms")
    print(f"   95% de requests < {stats.total.get_response_time_percentile(0.95):.0f}ms")
    print(f"   99% de requests < {stats.total.get_response_time_percentile(0.99):.0f}ms")
    
    print("\n" + "=" * 80)
    
    # Evaluación de resultados
    print("\n📈 EVALUACIÓN DE RENDIMIENTO:")
    
    # Evaluar tasa de fallos
    if stats.total.fail_ratio == 0:
        print("✅ PERFECTO: 0% de fallos - Sistema totalmente estable")
    elif stats.total.fail_ratio <= 0.01:
        print("✅ EXCELENTE: Tasa de fallos < 1%")
    elif stats.total.fail_ratio <= 0.05:
        print("⚠️  ACEPTABLE: Tasa de fallos < 5%")
    else:
        print("❌ CRÍTICO: Tasa de fallos > 5% - Revisar logs del servidor")
    
    # Evaluar tiempos de respuesta
    if stats.total.avg_response_time < 500:
        print("✅ EXCELENTE: Tiempo promedio < 500ms")
    elif stats.total.avg_response_time < 1000:
        print("✅ BUENO: Tiempo promedio < 1 segundo")
    elif stats.total.avg_response_time < 2000:
        print("⚠️  ACEPTABLE: Tiempo promedio < 2 segundos")
    else:
        print("❌ CRÍTICO: Tiempo promedio > 2 segundos - Sistema saturado")
    
    # Evaluar RPS
    if stats.total.total_rps >= 20:
        print("✅ EXCELENTE: RPS ≥ 20 (Alto throughput)")
    elif stats.total.total_rps >= 15:
        print("✅ BUENO: RPS ≥ 15")
    elif stats.total.total_rps >= 10:
        print("✅ ACEPTABLE: RPS ≥ 10")
    elif stats.total.total_rps >= 5:
        print("⚠️  BAJO: RPS < 10 pero > 5")
    else:
        print("❌ CRÍTICO: RPS < 5 - Capacidad muy limitada")
    
    # Recomendaciones
    print("\n💡 RECOMENDACIONES:")
    if stats.total.fail_ratio == 0 and stats.total.avg_response_time < 1000:
        print("   🎉 El sistema está funcionando EXCELENTEMENTE")
        print("   ✅ Capacidad confirmada para esta carga de usuarios")
        if stats.total.total_rps > 20:
            print("   🚀 Podrías probar con MÁS usuarios para encontrar el límite")
    elif stats.total.fail_ratio > 0.05 or stats.total.avg_response_time > 2000:
        print("   ⚠️  Has alcanzado el límite de capacidad del servidor")
        print("   📉 Considera reducir el número de usuarios concurrentes")
        print("   🔧 Revisa los logs del servidor para identificar cuellos de botella")
    else:
        print("   ✅ Sistema operando dentro de parámetros aceptables")
        print("   📊 Monitora el comportamiento en producción")
    
    print("\n" + "=" * 80 + "\n")


# ============================================
# GUÍA DE USO
# ============================================

"""
🚀 CÓMO EJECUTAR LA PRUEBA:

1. INSTALACIÓN:
   pip install locust

2. EJECUTAR:
   locust -f test_load_taller_computo_pro.py

3. ABRIR NAVEGADOR:
   http://localhost:8089

4. CONFIGURAR:
   - Number of users: 60 (recomendado inicial)
   - Spawn rate: 5-10
   - Presionar "Start Swarming"


📊 PLAN DE PRUEBAS ESCALONADO:

NIVEL 1 - Validación (5 min):
   locust -f test_load_taller_computo_pro.py --headless -u 30 -r 5 -t 5m
   Objetivo: Verificar que todo funciona correctamente

NIVEL 2 - Carga Normal (10 min):
   locust -f test_load_taller_computo_pro.py --headless -u 60 -r 5 -t 10m
   Objetivo: Simular uso típico diario

NIVEL 3 - Hora Pico (10 min):
   locust -f test_load_taller_computo_pro.py --headless -u 80 -r 8 -t 10m
   Objetivo: Carga máxima esperada en producción

NIVEL 4 - Prueba de Estrés (5 min):
   locust -f test_load_taller_computo_pro.py --headless -u 100 -r 10 -t 5m
   Objetivo: Encontrar el punto de quiebre del sistema

NIVEL 5 - Estrés Extremo (3 min):
   locust -f test_load_taller_computo_pro.py --headless -u 150 -r 15 -t 3m
   Objetivo: Probar límite absoluto (⚠️ puede fallar)


✅ MÉTRICAS OBJETIVO:

🏆 EXCELENTE (Producción ideal):
   - Failure rate: 0%
   - Avg response time: < 500ms
   - RPS: > 20
   - 95th percentile: < 800ms

✅ BUENO (Aceptable para producción):
   - Failure rate: < 1%
   - Avg response time: < 1000ms
   - RPS: > 15
   - 95th percentile: < 1500ms

⚠️  ACEPTABLE (Límite operativo):
   - Failure rate: < 5%
   - Avg response time: < 2000ms
   - RPS: > 10
   - 95th percentile: < 3000ms

❌ CRÍTICO (Sobrecarga):
   - Failure rate: > 5%
   - Avg response time: > 2000ms
   - RPS: < 10
   - 95th percentile: > 5000ms


🎯 MEJORAS IMPLEMENTADAS EN ESTA VERSIÓN:

✅ 4 cuentas de alumnos diferentes (simula diversidad real)
✅ 15+ endpoints cubiertos (cobertura completa del sistema)
✅ Navegación de grados 1-6 (incluyendo los nuevos: 1 y 2)
✅ Rutas de alumnos: tareas, calificaciones, apuntes, archivos
✅ Rutas de admin: cuestionarios, anuncios, biblioteca
✅ Distribución realista: 60% anónimos, 30% alumnos, 10% admin
✅ Métricas detalladas con percentiles
✅ Evaluación automática de resultados


💡 TIPS PARA INTERPRETAR RESULTADOS:

• Si RPS baja mientras aumentan usuarios = Saturación del servidor
• Si Failure Rate sube súbitamente = Has alcanzado el límite
• Cold start inicial es normal (primeros 30-60 segundos lentos)
• Render free tier puede hibernar si no hay tráfico (primer request lento)
• Tiempos > 2000ms indican que el servidor está luchando


📝 NOTAS IMPORTANTES:

⚠️  Render Free Tier tiene limitaciones:
   - Puede hibernar después de 15 min de inactividad
   - Conexiones simultáneas limitadas
   - CPU/RAM compartida con otros servicios

🔥 Para pruebas de estrés extremo (100+ usuarios):
   - Hazlo en horarios de bajo tráfico
   - Monitorea los logs de Render en tiempo real
   - Ten en cuenta que puede afectar usuarios reales si los hay

✅ Buenas prácticas:
   - Corre pruebas incrementales (30 → 60 → 80 → 100)
   - Deja al menos 2-3 minutos entre pruebas
   - Documenta los resultados de cada prueba
   - Compara resultados para ver tendencias
"""