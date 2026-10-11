"""GANTECH Pós-Venda — aplicativo independente (MVP)."""
import os, secrets, csv, io, json
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from functools import wraps
from urllib.parse import urlencode
import requests
from flask import Flask, request, session, redirect, url_for, render_template_string, flash, abort, Response
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import UniqueConstraint, func
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY') or secrets.token_hex(32)
uri = os.environ.get('DATABASE_URL', 'sqlite:///posvenda.db')
if uri.startswith('postgres://'): uri = 'postgresql://' + uri[len('postgres://'):]
app.config['SQLALCHEMY_DATABASE_URI'] = uri
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = os.environ.get('COOKIE_SECURE', 'false').lower() == 'true'
app.config['MAX_CONTENT_LENGTH'] = 3 * 1024 * 1024
db = SQLAlchemy(app)
ACCOUNTS = {'45066833': 'GANTECH', '1106389395': 'V3R'}
STATUSES = ['Aberta','Aguardando postagem','Em transporte','Recebida','Em análise','Contestação enviada','Reembolso pendente','Encerrada']
KINDS = ['Devolução','Reembolso','Cancelamento','Reclamação','Contestação']

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.Text, nullable=False)

class MLAccount(db.Model):
    seller_id = db.Column(db.String(40), primary_key=True)
    access_token = db.Column(db.Text, nullable=False)
    refresh_token = db.Column(db.Text)
    expires_at = db.Column(db.DateTime(timezone=True))
    updated_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

class Case(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    platform = db.Column(db.String(30), nullable=False, default='Mercado Livre')
    seller_id = db.Column(db.String(40), nullable=False)
    order_id = db.Column(db.String(90), nullable=False)
    claim_id = db.Column(db.String(90), nullable=False, default='')
    sku = db.Column(db.String(100), default='')
    product = db.Column(db.Text, default='')
    kind = db.Column(db.String(40), nullable=False, default='Devolução')
    status = db.Column(db.String(50), nullable=False, default='Aberta')
    reason = db.Column(db.Text, default='')
    owner = db.Column(db.String(100), default='')
    next_action = db.Column(db.Text, default='')
    due_date = db.Column(db.Date)
    sale_amount = db.Column(db.Numeric(12,2), default=0)
    refunded = db.Column(db.Numeric(12,2), default=0)
    fees = db.Column(db.Numeric(12,2), default=0)
    shipping = db.Column(db.Numeric(12,2), default=0)
    compensation = db.Column(db.Numeric(12,2), default=0)
    recovered_value = db.Column(db.Numeric(12,2), default=0)
    inspection = db.Column(db.Text, default='')
    evidence = db.Column(db.Text, default='')
    outcome = db.Column(db.Text, default='')
    updated_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    __table_args__ = (UniqueConstraint('platform','seller_id','order_id','claim_id',name='uq_case_key'),)
    @property
    def exposure(self):
        return sum(Decimal(str(x or 0)) for x in (self.refunded,self.fees,self.shipping)) - sum(Decimal(str(x or 0)) for x in (self.compensation,self.recovered_value))

class Audit(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    case_id = db.Column(db.Integer, db.ForeignKey('case.id'), nullable=False)
    username = db.Column(db.String(80), nullable=False)
    action = db.Column(db.String(40), nullable=False)
    detail = db.Column(db.Text)
    at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

with app.app_context():
    db.create_all()
    if not User.query.first():
        admin_user = os.environ.get('ADMIN_USERNAME','admin').strip()
        admin_pass = os.environ.get('ADMIN_PASSWORD','')
        if admin_pass:
            db.session.add(User(username=admin_user,password_hash=generate_password_hash(admin_pass)))
            db.session.commit()

@app.before_request
def csrf_check():
    if 'csrf' not in session: session['csrf'] = secrets.token_urlsafe(24)
    if request.method == 'POST' and request.endpoint not in ('oauth_callback',):
        if request.form.get('csrf') != session.get('csrf'): abort(400, 'Token de formulário inválido.')

def login_required(f):
    @wraps(f)
    def inner(*args,**kwargs):
        if not session.get('user'): return redirect(url_for('login'))
        return f(*args,**kwargs)
    return inner

BASE = '''<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>GANTECH Pós-Venda</title>
<style>body{font-family:Arial,sans-serif;background:#f5f6f8;color:#20242b;margin:0}header{background:#17191e;color:white;padding:17px 5%;display:flex;justify-content:space-between;align-items:center}header a{color:white;text-decoration:none;margin-left:18px}main{max-width:1250px;margin:26px auto;padding:0 18px}.card{background:white;border:1px solid #e3e6ea;border-radius:12px;padding:20px;margin:14px 0}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}.metric{font-size:27px;font-weight:bold;margin:10px 0}.muted{color:#667085;font-size:13px}table{border-collapse:collapse;width:100%;font-size:13px}th,td{border-bottom:1px solid #eee;text-align:left;padding:11px 8px}input,select,textarea{width:100%;box-sizing:border-box;border:1px solid #ccd1d8;border-radius:7px;padding:10px;margin:5px 0 13px}textarea{min-height:65px}label{font-weight:bold;font-size:13px}button,.btn{display:inline-block;background:#242831;color:white;border:0;padding:10px 16px;border-radius:7px;text-decoration:none;cursor:pointer}.secondary{background:#e9edf2;color:#20242b}.row{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:14px}.alert{padding:12px;background:#fff4df;border-radius:8px;margin:10px 0}a{color:#1764c1}.danger{color:#a52525}small{color:#667085}</style></head><body><header><strong>GANTECH · GESTÃO DE PÓS-VENDA</strong><nav>{% if session.get('user') %}<a href="{{url_for('dashboard')}}">Painel</a><a href="{{url_for('case_new')}}">Nova ocorrência</a><a href="{{url_for('accounts')}}">Contas ML</a><a href="{{url_for('import_csv')}}">Importar CSV</a><a href="{{url_for('export_csv')}}">Exportar</a><a href="{{url_for('logout')}}">Sair</a>{% endif %}</nav></header><main>{% for m in get_flashed_messages() %}<div class="alert">{{m}}</div>{% endfor %}{{content|safe}}</main></body></html>'''
def page(template, **data):
    body = render_template_string(template,**data)
    return render_template_string(BASE,content=body)

def money(v): return 'R$ '+f'{Decimal(str(v or 0)):,.2f}'.replace(',','X').replace('.',',').replace('X','.')
app.jinja_env.globals['money'] = money
app.jinja_env.globals['accounts_names'] = ACCOUNTS
app.jinja_env.globals['statuses'] = STATUSES
app.jinja_env.globals['kinds'] = KINDS

@app.route('/login',methods=['GET','POST'])
def login():
    if request.method == 'POST':
        u = User.query.filter_by(username=request.form.get('username','')).first()
        if u and check_password_hash(u.password_hash,request.form.get('password','')):
            session.clear(); session['user']=u.username; session['csrf']=secrets.token_urlsafe(24)
            return redirect(url_for('dashboard'))
        flash('Usuário ou senha inválidos.')
    return page('''<div class="card" style="max-width:380px;margin:70px auto"><h2>Acesso interno</h2><form method="post"><input type="hidden" name="csrf" value="{{session.csrf}}"><label>Usuário</label><input name="username" required><label>Senha</label><input name="password" type="password" required><button>Entrar</button></form></div>''')
@app.route('/logout')
def logout(): session.clear(); return redirect(url_for('login'))
@app.route('/')
@login_required
def dashboard():
    seller=request.args.get('seller',''); status=request.args.get('status',''); term=request.args.get('q','').strip()
    q=Case.query
    if seller: q=q.filter_by(seller_id=seller)
    if status: q=q.filter_by(status=status)
    if term: q=q.filter(db.or_(Case.order_id.ilike('%'+term+'%'),Case.sku.ilike('%'+term+'%'),Case.claim_id.ilike('%'+term+'%')))
    cases=q.order_by(Case.updated_at.desc()).limit(500).all()
    today=datetime.now(timezone.utc).date()
    overdue=sum(bool(c.due_date and c.due_date<today and c.status!='Encerrada') for c in cases)
    return page('''<h2>Painel de ocorrências</h2><div class="grid"><div class="card"><small>Ocorrências filtradas</small><div class="metric">{{cases|length}}</div></div><div class="card"><small>Em aberto</small><div class="metric">{{cases|selectattr('status','ne','Encerrada')|list|length}}</div></div><div class="card"><small>Prazos vencidos</small><div class="metric danger">{{overdue}}</div></div><div class="card"><small>Saldo estimado de perdas*</small><div class="metric">{{money(total)}}</div></div></div><small>* Reembolsos + tarifas + fretes − compensações − valor recuperado. Não representa necessariamente prejuízo contábil.</small><div class="card"><form method="get" class="row"><div><label>Conta</label><select name="seller"><option value="">Todas</option>{% for sid,name in accounts_names.items() %}<option value="{{sid}}" {% if seller==sid %}selected{% endif %}>{{name}}</option>{% endfor %}</select></div><div><label>Status</label><select name="status"><option value="">Todos</option>{% for s in statuses %}<option {% if status==s %}selected{% endif %}>{{s}}</option>{% endfor %}</select></div><div><label>Venda / SKU / reclamação</label><input name="q" value="{{term}}"></div><div style="align-self:center"><button>Filtrar</button></div></form><div style="overflow:auto"><table><thead><tr><th>Conta</th><th>Venda</th><th>SKU</th><th>Tipo</th><th>Status</th><th>Prazo</th><th>Responsável</th><th>Perda estimada</th></tr></thead><tbody>{% for c in cases %}<tr><td>{{accounts_names.get(c.seller_id,c.seller_id)}}</td><td><a href="{{url_for('case_edit',id=c.id)}}">{{c.order_id}}</a></td><td>{{c.sku}}</td><td>{{c.kind}}</td><td>{{c.status}}</td><td class="{% if c.due_date and c.due_date<today and c.status!='Encerrada' %}danger{% endif %}">{{c.due_date or '-'}}</td><td>{{c.owner}}</td><td>{{money(c.exposure)}}</td></tr>{% else %}<tr><td colspan="8">Nenhuma ocorrência cadastrada.</td></tr>{% endfor %}</tbody></table></div></div>''',cases=cases,overdue=overdue,total=sum((c.exposure for c in cases),Decimal(0)),seller=seller,status=status,term=term,today=today)

FIELDS=['platform','seller_id','order_id','claim_id','sku','product','kind','status','reason','owner','next_action','due_date','sale_amount','refunded','fees','shipping','compensation','recovered_value','inspection','evidence','outcome']
FINANCIAL=['sale_amount','refunded','fees','shipping','compensation','recovered_value']
def update_case(c,form):
    for f in FIELDS:
        v=(form.get(f) or '').strip()
        if f in FINANCIAL:
            try: v=Decimal(v.replace('.','').replace(',','.')) if ',' in v else Decimal(v or '0')
            except InvalidOperation: raise ValueError('Valor financeiro inválido: '+f)
        elif f=='due_date': v=datetime.strptime(v,'%Y-%m-%d').date() if v else None
        setattr(c,f,v)
    if c.seller_id not in ACCOUNTS: raise ValueError('Conta inválida')
    if not c.order_id: raise ValueError('Número da venda obrigatório')
    if c.kind not in KINDS or c.status not in STATUSES: raise ValueError('Tipo ou status inválido')

FORM='''<h2>{{'Editar ocorrência #'+(c.id|string) if c.id else 'Nova ocorrência'}}</h2><div class="card"><form method="post"><input type="hidden" name="csrf" value="{{session.csrf}}"><div class="row"><div><label>Plataforma</label><select name="platform"><option>Mercado Livre</option></select></div><div><label>Conta</label><select name="seller_id">{% for sid,name in accounts_names.items() %}<option value="{{sid}}" {% if c.seller_id==sid %}selected{% endif %}>{{name}}</option>{% endfor %}</select></div><div><label>Venda</label><input name="order_id" value="{{c.order_id or ''}}" required></div><div><label>Reclamação / contestação (ID)</label><input name="claim_id" value="{{c.claim_id or ''}}"></div><div><label>SKU</label><input name="sku" value="{{c.sku or ''}}"></div><div><label>Produto</label><input name="product" value="{{c.product or ''}}"></div><div><label>Tipo</label><select name="kind">{% for s in kinds %}<option {% if c.kind==s %}selected{% endif %}>{{s}}</option>{% endfor %}</select></div><div><label>Status</label><select name="status">{% for s in statuses %}<option {% if c.status==s %}selected{% endif %}>{{s}}</option>{% endfor %}</select></div><div><label>Responsável</label><input name="owner" value="{{c.owner or ''}}"></div><div><label>Prazo</label><input name="due_date" type="date" value="{{c.due_date or ''}}"></div></div><div class="row">{% for f,label in amounts %}<div><label>{{label}} (R$)</label><input name="{{f}}" type="number" step="0.01" value="{{c[f] or 0}}"></div>{% endfor %}</div>{% for f,label in notes %}<label>{{label}}</label><textarea name="{{f}}">{{c[f] or ''}}</textarea>{% endfor %}<button>Salvar ocorrência</button> <a class="btn secondary" href="{{url_for('dashboard')}}">Voltar</a></form></div>{% if audits %}<div class="card"><h3>Histórico</h3>{% for a in audits %}<p><small>{{a.at.strftime('%d/%m/%Y %H:%M')}} · {{a.username}} · {{a.action}}</small> — {{a.detail}}</p>{% endfor %}</div>{% endif %}'''
AMOUNTS=[('sale_amount','Venda'),('refunded','Reembolsado'),('fees','Tarifas'),('shipping','Fretes'),('compensation','Compensações'),('recovered_value','Valor recuperado do produto')]
NOTES=[('reason','Motivo informado'),('next_action','Próxima ação'),('inspection','Conferência física'),('evidence','Evidências / referências internas'),('outcome','Resultado da contestação')]
@app.route('/cases/new',methods=['GET','POST'])
@login_required
def case_new():
    c=Case(seller_id='45066833',order_id='',claim_id='',kind='Devolução',status='Aberta')
    if request.method=='POST':
        try:
            update_case(c,request.form); db.session.add(c); db.session.flush(); db.session.add(Audit(case_id=c.id,username=session['user'],action='Criada',detail=c.status)); db.session.commit(); flash('Ocorrência criada.'); return redirect(url_for('case_edit',id=c.id))
        except Exception as e: db.session.rollback(); flash('Não foi possível salvar: '+str(e))
    return page(FORM,c=c,amounts=AMOUNTS,notes=NOTES,audits=[])
@app.route('/cases/<int:id>',methods=['GET','POST'])
@login_required
def case_edit(id):
    c=db.get_or_404(Case,id)
    if request.method=='POST':
        try:
            before={'status':c.status,'owner':c.owner,'next_action':c.next_action}
            update_case(c,request.form)
            detail=json.dumps({'antes':before,'depois':{'status':c.status,'owner':c.owner,'next_action':c.next_action}},ensure_ascii=False)
            db.session.add(Audit(case_id=c.id,username=session['user'],action='Atualizada',detail=detail)); db.session.commit(); flash('Ocorrência atualizada.')
            return redirect(url_for('case_edit',id=id))
        except Exception as e: db.session.rollback(); flash('Não foi possível salvar: '+str(e))
    audits=Audit.query.filter_by(case_id=id).order_by(Audit.at.desc()).all()
    return page(FORM,c=c,amounts=AMOUNTS,notes=NOTES,audits=audits)

@app.route('/accounts')
@login_required
def accounts():
    rows=MLAccount.query.all(); connected={r.seller_id for r in rows}
    return page('''<h2>Contas Mercado Livre</h2><div class="card"><p>As contas são autorizadas separadamente por OAuth. O sistema não reutiliza nem modifica os tokens da Central Omnichannel.</p>{% for sid,name in accounts_names.items() %}<p><strong>{{name}}</strong> · {{sid}} · {{'Autorizada' if sid in connected else 'Não autorizada'}} · <a class="btn secondary" href="{{url_for('oauth_start',seller_id=sid)}}">Autorizar conta</a></p>{% endfor %}<small>A autorização OAuth é opcional nesta primeira versão. A sincronização automática de reclamações e reembolsos ainda não está implementada.</small></div>''',connected=connected)
@app.route('/oauth/start/<seller_id>')
@login_required
def oauth_start(seller_id):
    if seller_id not in ACCOUNTS: abort(404)
    if not all(os.environ.get(k) for k in ['ML_CLIENT_ID','ML_CLIENT_SECRET','ML_REDIRECT_URI']): abort(503,'Configure ML_CLIENT_ID, ML_CLIENT_SECRET e ML_REDIRECT_URI.')
    state=secrets.token_urlsafe(32); session['oauth_state']=state; session['oauth_seller']=seller_id
    params={'response_type':'code','client_id':os.environ['ML_CLIENT_ID'],'redirect_uri':os.environ['ML_REDIRECT_URI'],'state':state}
    return redirect('https://auth.mercadolivre.com.br/authorization?'+urlencode(params))
@app.route('/oauth/callback')
@login_required
def oauth_callback():
    if not request.args.get('state') or request.args.get('state')!=session.pop('oauth_state',None): abort(400,'OAuth state inválido')
    expected=session.pop('oauth_seller',None); code=request.args.get('code')
    if not code: abort(400,'Código OAuth ausente')
    r=requests.post('https://api.mercadolibre.com/oauth/token',data={'grant_type':'authorization_code','client_id':os.environ['ML_CLIENT_ID'],'client_secret':os.environ['ML_CLIENT_SECRET'],'code':code,'redirect_uri':os.environ['ML_REDIRECT_URI']},timeout=20)
    if not r.ok: abort(502,'Falha ao autorizar conta no Mercado Livre')
    data=r.json(); sid=str(data.get('user_id',''))
    if sid!=expected: abort(403,'Conta autorizada não corresponde à conta selecionada.')
    row=db.session.get(MLAccount,sid) or MLAccount(seller_id=sid)
    row.access_token=data['access_token']; row.refresh_token=data.get('refresh_token') or row.refresh_token
    row.expires_at=datetime.now(timezone.utc)+timedelta(seconds=int(data.get('expires_in') or 0)); row.updated_at=datetime.now(timezone.utc)
    db.session.add(row); db.session.commit(); flash('Conta '+ACCOUNTS[sid]+' autorizada.'); return redirect(url_for('accounts'))

CSV_FIELDS=FIELDS
@app.route('/export.csv')
@login_required
def export_csv():
    output=io.StringIO(); w=csv.writer(output,delimiter=';'); w.writerow(CSV_FIELDS)
    for c in Case.query.order_by(Case.id).all(): w.writerow([getattr(c,f) if getattr(c,f) is not None else '' for f in CSV_FIELDS])
    return Response('\ufeff'+output.getvalue(),mimetype='text/csv; charset=utf-8',headers={'Content-Disposition':'attachment; filename=gantech_posvenda.csv'})
@app.route('/import',methods=['GET','POST'])
@login_required
def import_csv():
    if request.method=='POST':
        f=request.files.get('file')
        if not f or not f.filename.lower().endswith('.csv'): flash('Envie um arquivo CSV.'); return redirect(url_for('import_csv'))
        try:
            raw=f.read().decode('utf-8-sig'); reader=csv.DictReader(io.StringIO(raw),delimiter=';')
            if not {'seller_id','order_id','kind','status'}.issubset(set(reader.fieldnames or [])): raise ValueError('CSV deve conter seller_id, order_id, kind e status. Utilize o modelo exportado pelo sistema.')
            count=0
            for row in reader:
                seller=row.get('seller_id',''); order=row.get('order_id',''); claim=row.get('claim_id','') or ''
                c=Case.query.filter_by(platform='Mercado Livre',seller_id=seller,order_id=order,claim_id=claim).first()
                if not c: c=Case(); db.session.add(c)
                update_case(c,{k:row.get(k,'') for k in FIELDS}); db.session.flush()
                db.session.add(Audit(case_id=c.id,username=session['user'],action='Importada',detail='CSV manual')); count+=1
            db.session.commit(); flash(f'{count} linhas importadas/atualizadas.'); return redirect(url_for('dashboard'))
        except Exception as e: db.session.rollback(); flash('Falha na importação: '+str(e))
    return page('''<h2>Importação CSV</h2><div class="card"><p>Importe o CSV no formato do próprio sistema. A chave conta + venda + reclamação impede duplicação em reimportações. <strong>Planilhas nativas do Mercado Livre ainda precisam de um mapeador específico.</strong></p><p><a href="{{url_for('export_csv')}}">Baixar modelo CSV</a></p><form method="post" enctype="multipart/form-data"><input type="hidden" name="csrf" value="{{session.csrf}}"><input type="file" name="file" accept=".csv" required><button>Importar</button></form></div>''')
@app.route('/health')
def health(): return {'ok':True,'app':'gantech-posvenda','version':'1.0.0'}
if __name__=='__main__': app.run(debug=False)
