# G:\Learning\F1Data\F1Data_App\core\models.py
from django.db import models
from django.contrib.postgres.fields import ArrayField

class Meetings(models.Model):
    meeting_key = models.IntegerField(primary_key=True)
    circuit_key = models.IntegerField(null=True, blank=True)
    circuit_short_name = models.CharField(max_length=50, null=True, blank=True)
    meeting_code = models.CharField(max_length=10, null=True, blank=True)
    location = models.CharField(max_length=100, null=True, blank=True)
    country_key = models.IntegerField(null=True, blank=True)
    country_code = models.CharField(max_length=10, null=True, blank=True)
    country_name = models.CharField(max_length=100, null=True, blank=True)
    meeting_name = models.CharField(max_length=255, null=True, blank=True)
    meeting_official_name = models.CharField(max_length=255, null=True, blank=True)
    gmt_offset = models.CharField(max_length=20, null=True, blank=True)
    date_start = models.DateTimeField(null=True, blank=True)
    year = models.IntegerField(null=True, blank=True)
    class Meta:
        managed = False 
        db_table = 'meetings' # O nome exato da tabela no seu banco de dados
        verbose_name_plural = 'Meetings' # Boa prática para o admin

class Sessions(models.Model):
    # Uma das colunas da PK deve ser marcada explicitamente como PK
    session_key = models.IntegerField(primary_key=True)
    meeting_key = models.IntegerField()
    location = models.CharField(max_length=100, null=True)
    date_start = models.DateTimeField(null=True)
    date_end = models.DateTimeField(null=True)
    session_type = models.CharField(max_length=50, null=True)
    session_name = models.CharField(max_length=100)
    country_key = models.IntegerField(null=True)
    country_code = models.CharField(max_length=10, null=True)
    country_name = models.CharField(max_length=100, null=True)
    circuit_key = models.IntegerField(null=True)
    circuit_short_name = models.CharField(max_length=100, null=True)
    gmt_offset = models.CharField(max_length=10, null=True)
    year = models.IntegerField(null=True)
    class Meta:
        db_table = 'sessions'
        managed = False
        # 'unique_together' só para complementar se quiser manter unicidade total
        unique_together = (('meeting_key', 'session_key', 'session_name'),)
    def __str__(self):
        return f"Session {self.session_name} (meeting_key={self.meeting_key})"
    
class Drivers(models.Model):
    # driver_number é parte da PK composta e será usado como primary_key=True para o Django
    driver_number = models.IntegerField(primary_key=True)
    meeting_key = models.IntegerField() # NOT NULL na PK composta
    session_key = models.IntegerField() # NOT NULL na PK composta
    broadcast_name = models.CharField(max_length=100, null=True, blank=True)
    full_name = models.CharField(max_length=100, null=True, blank=True)
    name_acronym = models.CharField(max_length=10, null=True, blank=True)
    team_name = models.CharField(max_length=100, null=True, blank=True)
    team_colour = models.CharField(max_length=10, null=True, blank=True)
    first_name = models.CharField(max_length=50, null=True, blank=True)
    last_name = models.CharField(max_length=50, null=True, blank=True)
    headshot_url = models.TextField(null=True, blank=True) # Text é para campos maiores
    country_code = models.CharField(max_length=10, null=True, blank=True)
    class Meta:
        managed = False  # Django NÃO vai gerenciar a criação/alteração desta tabela
        db_table = 'drivers'  # Nome exato da tabela no banco de dados
        # Define a chave primária composta real para o banco de dados
        unique_together = (('meeting_key', 'session_key', 'driver_number'),)
        verbose_name_plural = 'Drivers' # Nome para o admin do Django
    def __str__(self):
        # Retorna uma representação legível do objeto Driver
        # Por exemplo: "VER - Max Verstappen (1)"
        return f"{self.name_acronym} - {self.full_name} ({self.driver_number})"

class RaceControl(models.Model):
    # Campos da chave única composta (e PK interna para Django)
    meeting_key = models.IntegerField()
    session_key = models.IntegerField()
    session_date = models.DateTimeField(primary_key=True) # Designada como PK interna do Django
    driver_number = models.IntegerField(blank=True, null=True)
    lap_number = models.IntegerField(blank=True, null=True)
    category = models.CharField(max_length=50, blank=True, null=True)
    flag = models.CharField(max_length=50, blank=True, null=True)
    scope = models.CharField(max_length=50, blank=True, null=True, db_column='"scope"') # 'scope' é palavra-chave SQL
    sector = models.IntegerField(blank=True, null=True)
    message = models.TextField(blank=True, null=True)

    class Meta:
        db_table = 'racecontrol'
        managed = False
        unique_together = (
            ('meeting_key', 'session_key', 'driver_number', 'lap_number', 'category', 'flag', 'sector'),
        )
        # verbose_name e verbose_name_plural foram removidos
        ordering = ['session_date']

    def __str__(self):
        return f"Race Control (Mtg: {self.meeting_key}, Sess: {self.session_key}, Data: {self.session_date.isoformat()})"

class TeamRadio(models.Model):
    # meeting_key é o primeiro campo da PK composta, então o usamos como primary_key=True para o Django
    meeting_key = models.IntegerField(primary_key=True) # NOT NULL na PK composta
    session_key = models.IntegerField() # NOT NULL na PK composta
    driver_number = models.IntegerField() # NOT NULL na PK composta
    date = models.DateTimeField() # NOT NULL na PK composta (corresponde a 'date' no JSON)
    recording_url = models.TextField(null=True, blank=True) # URL da gravação
    class Meta:
        managed = False  # Django NÃO vai gerenciar a criação/alteração desta tabela
        db_table = 'teamradio'  # Nome exato da tabela no banco de dados
        # Define a chave primária composta real para o banco de dados
        unique_together = (('meeting_key', 'session_key', 'driver_number', 'date'),)
        verbose_name_plural = 'Team Radio'
    def __str__(self):
        # Retorna uma representação legível
        return f"TR: Mtg {self.meeting_key}, Sess {self.session_key}, Driver {self.driver_number} ({self.date.strftime('%Y-%m-%d %H:%M')})"
    
class Weather(models.Model):
    # session_key é o primeiro campo da PK composta, então o usamos como primary_key=True para o Django
    session_key = models.IntegerField(primary_key=True) # NOT NULL na PK composta
    meeting_key = models.IntegerField() # NOT NULL na PK composta
    session_date = models.DateTimeField() # NOT NULL na PK composta (corresponde a 'date' no JSON)
    wind_direction = models.IntegerField(null=True, blank=True)
    air_temperature = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    humidity = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    pressure = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    rainfall = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    wind_speed = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    track_temperature = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    class Meta:
        managed = False  # Django NÃO vai gerenciar a criação/alteração desta tabela
        db_table = 'weather'  # Nome exato da tabela no banco de dados
        # Define a chave primária composta real para o banco de dados
        unique_together = (('session_key', 'meeting_key', 'session_date'),)
        verbose_name_plural = 'Weather'
    def __str__(self):
        # Retorna uma representação legível
        return f"Weather: Mtg {self.meeting_key}, Sess {self.session_key} ({self.session_date.strftime('%Y-%m-%d %H:%M')})"
    
class CarData(models.Model):
    # 'date' é o primeiro campo da PK composta no DDL, então o usamos como primary_key=True para o Django
    date = models.DateTimeField(primary_key=True) # NOT NULL na PK composta
    session_key = models.IntegerField() # NOT NULL na PK composta
    meeting_key = models.IntegerField() # NOT NULL na PK composta
    driver_number = models.IntegerField() # NOT NULL na PK composta
    speed = models.IntegerField(null=True, blank=True)
    n_gear = models.IntegerField(null=True, blank=True)
    drs = models.IntegerField(null=True, blank=True)
    throttle = models.IntegerField(null=True, blank=True)
    brake = models.IntegerField(null=True, blank=True)
    rpm = models.IntegerField(null=True, blank=True)
    class Meta:
        managed = False  # Django NÃO vai gerenciar a criação/alteração desta tabela
        db_table = 'cardata'  # Nome exato da tabela no banco de dados
        # Define a chave primária composta real para o banco de dados
        unique_together = (('date', 'session_key', 'meeting_key', 'driver_number'),)
        verbose_name_plural = 'Car Data'
    def __str__(self):
        # Retorna uma representação legível
        return f"CarData: Mtg {self.meeting_key}, Sess {self.session_key}, Driver {self.driver_number} ({self.date.strftime('%Y-%m-%d %H:%M:%S.%f')})"
    
class Location(models.Model):
    # Campos da PK composta
    # 'date' é o primeiro campo da PK composta no DDL, então o usamos como primary_key=True para o Django
    date = models.DateTimeField(primary_key=True) # NOT NULL na PK composta
    session_key = models.IntegerField() # NOT NULL na PK composta
    meeting_key = models.IntegerField() # NOT NULL na PK composta
    driver_number = models.IntegerField() # NOT NULL na PK composta
    z = models.IntegerField(null=True, blank=True)
    x = models.IntegerField(null=True, blank=True)
    y = models.IntegerField(null=True, blank=True)
    class Meta:
        managed = False  # Django NÃO vai gerenciar a criação/alteração desta tabela
        db_table = 'location'  # Nome exato da tabela no banco de dados
        # Define a chave primária composta real para o banco de dados
        unique_together = (('date', 'session_key', 'meeting_key', 'driver_number'),)
        verbose_name_plural = 'Location'
    def __str__(self):
        # Retorna uma representação legível
        return f"Loc: Mtg {self.meeting_key}, Sess {self.session_key}, Driver {self.driver_number} ({self.date.strftime('%Y-%m-%d %H:%M:%S.%f')})"
    
class Intervals(models.Model):
    session_key = models.IntegerField(primary_key=True)
    meeting_key = models.IntegerField()
    driver_number = models.IntegerField()
    date = models.DateTimeField() # NOT NULL na PK composta
    gap_to_leader = models.CharField(max_length=12, null=True, blank=True)
    interval_value = models.CharField(max_length=12, db_column='interval', null=True, blank=True)
    class Meta:
        managed = False
        db_table = 'intervals'
        unique_together = (('session_key', 'meeting_key', 'driver_number', 'date'),)
        verbose_name_plural = 'Intervals'
    def __str__(self):
        return f"Intervals: Mtg {self.meeting_key}, Sess {self.session_key}, Driver {self.driver_number} ({self.date.strftime('%Y-%m-%d %H:%M:%S.%f')})"
    
class Laps(models.Model):
    # Campos da PK composta
    # meeting_key é o primeiro campo da PK composta no DDL, então o usamos como primary_key=True para o Django
    meeting_key = models.IntegerField(primary_key=True) # NOT NULL na PK composta
    session_key = models.IntegerField() # NOT NULL na PK composta
    driver_number = models.IntegerField() # NOT NULL na PK composta
    lap_number = models.IntegerField() # NOT NULL na PK composta
    date_start = models.DateTimeField(null=True, blank=True)
    duration_sector_1 = models.DecimalField(max_digits=8, decimal_places=3, null=True, blank=True)
    duration_sector_2 = models.DecimalField(max_digits=8, decimal_places=3, null=True, blank=True)
    duration_sector_3 = models.DecimalField(max_digits=8, decimal_places=3, null=True, blank=True)
    i1_speed = models.IntegerField(null=True, blank=True)
    i2_speed = models.IntegerField(null=True, blank=True)
    is_pit_out_lap = models.BooleanField(null=True, blank=True) # BooleanField
    lap_duration = models.DecimalField(max_digits=8, decimal_places=3, null=True, blank=True)
    segments_sector_1 = ArrayField(models.IntegerField(), null=True, blank=True)
    segments_sector_2 = ArrayField(models.IntegerField(), null=True, blank=True)
    segments_sector_3 = ArrayField(models.IntegerField(), null=True, blank=True)
    st_speed = models.IntegerField(null=True, blank=True)
    class Meta:
        managed = False  # Django NÃO vai gerenciar a criação/alteração desta tabela
        db_table = 'laps'  # Nome exato da tabela no banco de dados
        # Define a chave primária composta real para o banco de dados
        unique_together = (('meeting_key', 'session_key', 'driver_number', 'lap_number'),)
        verbose_name_plural = 'Laps'
    def __str__(self):
        # Retorna uma representação legível
        return f"Lap: Mtg {self.meeting_key}, Sess {self.session_key}, Driver {self.driver_number}, Lap {self.lap_number}"
    
class Pit(models.Model):
    # Campos da PK composta
    # session_key é o primeiro campo da PK composta no DDL, então o usamos como primary_key=True para o Django
    session_key = models.IntegerField(primary_key=True) # NOT NULL na PK composta
    meeting_key = models.IntegerField() # NOT NULL na PK composta
    driver_number = models.IntegerField(null=True, blank=True) 
    lap_number = models.IntegerField() # NOT NULL na PK composta
    date = models.DateTimeField() # NOT NULL na PK composta
    pit_duration = models.DecimalField(max_digits=8, decimal_places=3, null=True, blank=True) # Numeric(8,3) e pode ser NULL
    class Meta:
        managed = False  # Django NÃO vai gerenciar a criação/alteração desta tabela
        db_table = 'pit'  # Nome exato da tabela no banco de dados
        # Define a chave primária composta real para o banco de dados
        unique_together = (('session_key', 'meeting_key', 'driver_number', 'date'),)
        verbose_name_plural = 'Pit' # Nome amigável para o admin do Django
    def __str__(self):
        # Retorna uma representação legível
        return f"Pit Stop: Mtg {self.meeting_key}, Sess {self.session_key}, Driver {self.driver_number}, Lap {self.lap_number}"

class Stint(models.Model):
    # meeting_key é o primeiro campo da PK composta no DDL, então o usamos como primary_key=True para o Django
    meeting_key = models.IntegerField(primary_key=True) # NOT NULL na PK composta
    session_key = models.IntegerField() # NOT NULL na PK composta
    stint_number = models.IntegerField() # NOT NULL na PK composta
    driver_number = models.IntegerField() # NOT NULL na PK composta
    lap_start = models.IntegerField(null=True, blank=True)
    lap_end = models.IntegerField(null=True, blank=True)
    compound = models.CharField(max_length=50, null=True, blank=True)
    tyre_age_at_start = models.IntegerField(null=True, blank=True)
    class Meta:
        managed = False  # Django NÃO vai gerenciar a criação/alteração desta tabela
        db_table = 'stint'  # Nome exato da tabela no banco de dados
        # Define a chave primária composta real para o banco de dados
        unique_together = (('meeting_key', 'session_key', 'stint_number', 'driver_number'),)
        verbose_name_plural = 'Stints'
    def __str__(self):
        # Retorna uma representação legível
        return f"Stint: Mtg {self.meeting_key}, Sess {self.session_key}, Stint {self.stint_number}, Driver {self.driver_number}"
    
class Position(models.Model):
    date = models.DateTimeField() # NOT NULL na PK composta
    driver_number = models.IntegerField() # NOT NULL na PK composta
    meeting_key = models.IntegerField() # NOT NULL na PK composta
    session_key = models.IntegerField() # NOT NULL na PK composta

    position = models.IntegerField(null=True, blank=True) # Pode ser null se a API retornar null

    class Meta:
        managed = False  # Django NÃO vai gerenciar a criação/alteração desta tabela
        db_table = 'positions'  # Nome exato da tabela no banco de dados (usei 'positions' para evitar conflito com palavra reservada)
        # Define a chave primária composta real para o banco de dados
        unique_together = (('meeting_key', 'session_key', 'driver_number', 'date'),) # Ordem da PK no DDL
        verbose_name_plural = 'Positions'

    def __str__(self):
        # Retorna uma representação legível
        return f"Position: Mtg {self.meeting_key}, Sess {self.session_key}, Driver {self.driver_number}, Pos {self.position} ({self.date.strftime('%Y-%m-%d %H:%M:%S.%f')})"

class SessionResult(models.Model):
    # --- CAMPOS DA CHAVE PRIMÁRIA COMPOSTA DO BANCO DE DADOS ---
    # Um dos campos é marcado com primary_key=True para o Django ORM
    # Isso resolve o erro 'id' e informa ao Django como identificar unicamente uma linha.
    meeting_key = models.IntegerField(
        primary_key=True,  # <--- ESCOLHIDO COMO CHAVE PRIMÁRIA INTERNA DO DJANGO
        verbose_name="Chave da Reunião",
        help_text="O identificador único para o evento (meeting)."
    )
    session_key = models.IntegerField(
        # Este campo faz parte da PK composta no seu DB, mas não é a PK interna do Django
        verbose_name="Chave da Sessão",
        help_text="O identificador único para a sessão."
    )
    driver_number = models.IntegerField(
        # Este campo faz parte da PK composta no seu DB, mas não é a PK interna do Django
        verbose_name="Número do Piloto",
        help_text="O número único atribuído a um piloto de F1."
    )

    # --- DEMAIS CAMPOS DE DADOS DA TABELA `sessionresult` ---
    position = models.CharField( # <-- ALTERADO: de IntegerField para CharField
        max_length=10,         # <-- NOVO: Defina um tamanho máximo para a string
        blank=True,
        null=True,
        verbose_name="Posição Final",
        help_text="A posição final do piloto no final da sessão."
    )
    number_of_laps = models.IntegerField(
        blank=True,
        null=True,
        verbose_name="Número de Voltas",
        help_text="Número total de voltas completadas durante a sessão."
    )
    dnf = models.BooleanField(
        default=False,
        verbose_name="Não Terminou",
        help_text="Indica se o piloto Não Terminou a corrida (apenas para sessões de corrida)."
    )
    dns = models.BooleanField(
        default=False,
        verbose_name="Não Iniciou",
        help_text="Indica se o piloto Não Iniciou a corrida (apenas para sessões de corrida ou qualificação)."
    )
    dsq = models.BooleanField(
        default=False,
        verbose_name="Desqualificado",
        help_text="Indica se o piloto foi desqualificado."
    )
    duration = ArrayField(
        models.DecimalField(max_digits=8, decimal_places=3, null=True, blank=True), # Permite valores nulos dentro do array
        blank=True,
        null=True,
        verbose_name="Duração/Melhor Tempo",
        help_text="Melhor tempo de volta (treino/qualificação) ou tempo total (corridas), em segundos. Array de 3 valores para Q1, Q2, e Q3."
    )
    gap_to_leader = ArrayField(
        models.TextField(null=True, blank=True), # Permite valores nulos dentro do array, e strings como '+N LAP(S)'
        blank=True,
        null=True,
        verbose_name="Diferença para o Líder",
        help_text="Diferença de tempo para o líder da sessão em segundos, ou '+N LAP(S)' se o piloto foi voltado. Array de 3 valores para Q1, Q2, e Q3."
    )

    class Meta:
        db_table = 'sessionresult'  # Nome real da tabela no banco de dados
        managed = False  # Informa ao Django para não gerenciar esta tabela via migrations
        
        # unique_together agora inclui APENAS os campos que, combinados com a primary_key (meeting_key),
        # garantem a unicidade da CHAVE PRIMÁRIA COMPOSTA do banco de dados (session_key, driver_number)
        unique_together = (('session_key', 'driver_number'),) # <--- AJUSTADO AQUI

        verbose_name = "Resultado da Sessão"
        verbose_name_plural = "Resultados da Sessão"

    def __str__(self):
        return f"Sessão {self.session_key} - Piloto {self.driver_number} - Posição {self.position}"


class StartingGrid(models.Model):
    meeting_key = models.IntegerField(primary_key=True)
    session_key = models.IntegerField()
    driver_number = models.IntegerField()
    position = models.IntegerField(null=True, blank=True)
    lap_duration = models.DecimalField(max_digits=8, decimal_places=3, null=True, blank=True)

    class Meta:
        managed = False
        db_table = 'startinggrid'
        unique_together = (('meeting_key', 'session_key', 'driver_number'),)
        verbose_name_plural = 'Starting Grid'

    def __str__(self):
        return f"Starting Grid: Mtg {self.meeting_key}, Sess {self.session_key}, Driver {self.driver_number}, Pos {self.position}"
    
class Circuit(models.Model):
    circuitid = models.IntegerField(primary_key=True)
    circuitref = models.CharField(max_length=50, unique=True)
    name = models.CharField(max_length=50, null=True, db_column='name')
    location = models.CharField(max_length=50, null=True)
    country = models.CharField(max_length=50, null=True)
    lat = models.FloatField(null=True)
    lng = models.FloatField(null=True)
    alt = models.IntegerField(null=True)
    url = models.CharField(max_length=128, null=True)

    class Meta:
        managed = False  # Django não controla criação/alteração da tabela
        db_table = 'circuits'  # schema + tabela
        unique_together = (('circuitref',),)  # índice único para circuitref (opcional, já tem unique=True)

    def __str__(self):
        return f"{self.name} ({self.circuitref})"