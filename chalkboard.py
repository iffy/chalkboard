from zope.interface import implements

from twisted.cred.portal import IRealm, Portal
from twisted.cred.checkers import FilePasswordDB
from twisted.web.static import File
from twisted.web.util import Redirect
from twisted.web.guard import HTTPAuthSessionWrapper, DigestCredentialFactory
from twisted.web.resource import Resource, IResource, NoResource, ForbiddenResource as Forbidden
from twisted.web.server import NOT_DONE_YET
from twisted.internet import defer
from twisted.enterprise.adbapi import ConnectionPool
from twisted.python import log

from collections import defaultdict
from functools import partial

from jinja2 import Environment, FileSystemLoader

import json



jenv = Environment(loader=FileSystemLoader('.'))

def render(name, params=None):
    template = jenv.get_template(name)
    return template.render(params or {}).encode('utf-8')



def getReadySqlite(connstr):
    pool = ConnectionPool('pysqlite2.dbapi2', connstr,
                          cp_min=1, cp_max=1)
    def interaction(c):
        try:
            c.execute('''create table sticky (
                    id integer primary key,
                    board_id text,
                    updated timestamp default current_timestamp,
                    note text,
                    x integer,
                    y integer)''')
        except Exception as e:
            log.err(e)
    return pool.runInteraction(interaction).addCallbacks((lambda x:pool), log.err)


def getReadyPostgres(connstr):
    pool = ConnectionPool('psycopg2', connstr)
    def interaction(c):
        try:
            c.execute('''create table sticky (
                    id serial primary key,
                    board_id text,
                    updated timestamp default current_timestamp,
                    note text,
                    x integer,
                    y integer)''')
        except Exception as e:
            log.err(e)
    return pool.runInteraction(interaction).addCallbacks((lambda x:pool), log.err)


class DatabaseBoardStore(object):

    paramstyle = '%s'
    tablename = 'sticky'
    pool = None


    def __init__(self, paramstyle='%s'):
        self.pending = []
        self.paramstyle = paramstyle


    def attachPool(self, pool):
        self.pool = pool
        self.runPending()


    def runPending(self):
        if not self.pool:
            return
        for d,name,args in self.pending:
            method = getattr(self.pool, name)
            method(*args).addBoth(lambda x:d.callback(x))
        while self.pending:
            self.pending.pop()

    def queue(self, name, args):
        d = defer.Deferred()
        self.pending.append((d, name, args))
        self.runPending()
        return d


    def runQuery(self, *args):
        return self.queue('runQuery', args)


    def runInteraction(self, *args):
        return self.queue('runInteraction', args)


    def formatRawQuery(self, qry):
        return qry % {'param': self.paramstyle}


    def getStickies(self, board_id):
        qry = self.formatRawQuery('select * from sticky '
                                  'where board_id = %(param)s '
                                  'order by updated')
        return self.runQuery(qry, (board_id,)).addCallback(self._gotStickies)


    def getSticky(self, sticky_id, board_id):
        select = self.formatRawQuery('''select * from sticky
                                      where id=%(param)s and board_id=%(param)s''')
        return self.runQuery(select, (sticky_id, board_id)).addCallback(self._gotOneRow)


    def _gotStickies(self, results):
        return [self._gotOneRow([x]) for x in results]


    def addSticky(self, board_id, note, x, y):
        insert = self.formatRawQuery('''insert into sticky (board_id, note, x, y)
                                     values(%(param)s, %(param)s, %(param)s, %(param)s)''')
        def interaction(c):
            c.execute(insert, (board_id, note, x, y))
            return c.lastrowid
        
        d = self.runInteraction(interaction)
        return d.addCallback(self.getSticky, board_id)


    def _gotOneRow(self, row):
        row = row[0]
        id, board_id, updated, note, x, y = row
        return {
            'id': id,
            'note': note,
            'x': x,
            'y': y,
        }


    def updateSticky(self, board_id, sticky_id, note, x, y):
        update = self.formatRawQuery('''update sticky set note=%(param)s,
                                        x=%(param)s, y=%(param)s,
                                        updated=current_timestamp
                                        where board_id=%(param)s and id=%(param)s''')
        d = self.runQuery(update, (note, x, y, board_id, sticky_id))
        d.addCallback(lambda x:sticky_id)
        return d.addCallback(self.getSticky, board_id)


    def removeSticky(self, board_id, sticky_id):
        delete = self.formatRawQuery('''delete from sticky where
                                        board_id=%(param)s and id=%(param)s''')
        d = self.runQuery(delete, (board_id, sticky_id))
        return d.addCallback(lambda x:sticky_id)



class EventStoreWrapper(object):
    """
    I provide event notification to a store.
    """
    
    def __init__(self, store):
        self.store = store
        self.subscriptions = defaultdict(lambda:[])


    def getStickies(self, board_id):
        return defer.maybeDeferred(self.store.getStickies, board_id)


    def addSticky(self, board_id, *args):
        return defer.maybeDeferred(self.store.addSticky,
                                   board_id, *args).addCallback(self.emitOne, board_id, 'add')


    def updateSticky(self, board_id, *args):
        return defer.maybeDeferred(self.store.updateSticky,
                                   board_id, *args).addCallback(self.emitOne, board_id, 'update')


    def removeSticky(self, board_id, *args):
        return defer.maybeDeferred(self.store.removeSticky,
                                   board_id, *args).addCallback(self.emitOne, board_id, 'remove')


    def emit(self, board_id, action, data):
        for s in list(self.subscriptions[board_id]):
            try:
                s(action, data)
            except Exception as e:
                print 'Error sending to subscription %s' % (e,)
                self.unsubscribe(board_id, s)


    def subscribe(self, board_id, func):
        self.subscriptions[board_id].append(func)


    def unsubscribe(self, board_id, func):
        self.subscriptions[board_id].remove(func)


    def emitOne(self, record, board_id, action):
        self.emit(board_id, action, record)
        return record
        


class InMemoryBoardStore(object):

    id = 0

    def __init__(self):
        self.boards = defaultdict(lambda:[])
        self.subscriptions = defaultdict(lambda:[])


    def nextId(self):
        self.id += 1
        return self.id


    def getStickies(self, board_id):
        return self.boards[board_id]


    def addSticky(self, board_id, note, x, y):
        id = self.nextId()
        self.boards[board_id].append({
            'id': id,
            'note': note,
            'x': x,
            'y': y,
        })
        sticky = self.boards[board_id][-1]
        return sticky


    def removeSticky(self, board_id, sticky_id):
        found = [x for x in self.boards[board_id] if x['id'] == sticky_id][0]
        self.boards[board_id].remove(found)
        return sticky_id


    def updateSticky(self, board_id, sticky_id, note, x, y):
        sticky = [s for s in self.boards[board_id] if s['id'] == sticky_id][0]
        sticky.update({
            'note': note,
            'x': x,
            'y': y,
        })
        self.boards[board_id].remove(sticky)
        self.boards[board_id].append(sticky)
        return sticky



def jsonfinish(data, request):
    request.setHeader('Content-type', 'application/json')
    request.write(json.dumps(data).encode('utf-8'))
    request.finish()


class ChalkboardIndex(Resource):

    def __init__(self, store, allowed):
        Resource.__init__(self)
        self.allowed = allowed
        self.store = store


    def render_GET(self, request):
        if len(self.allowed) == 1:
            request.redirect(request.childLink(self.allowed[0]))
            return ''
        links = ['<li><a href="%s">%s</a></li>' % (request.childLink(x),x) for x in self.allowed]
        return '''<html>
            <body>
                <ul>
                    %s
                </ul>
            </body>
        </html>''' % (''.join(links),)


    def getChild(self, name, request):
        if name not in self.allowed:
            return Forbidden()
        return Chalkboard(name, self.store)


    def getChildWithDefault(self, name, request):
        if name not in self.allowed:
            return Forbidden()
        return Chalkboard(name, self.store)


class ChalkboardRealm(object):
    implements(IRealm)
    
    def __init__(self, store, permissions):
        """
        @param permissions: A dictionary of permissions
        """
        self.store = store
        self.permissions = permissions

    def requestAvatar(self, avatarId, mind, *interfaces):
        if IResource in interfaces:
            return (IResource,
                    ChalkboardIndex(self.store, self.permissions.get(avatarId)),
                    lambda: None)
        raise NotImplementedError()



class Chalkboard(Resource):


    def __init__(self, board_id, store):
        Resource.__init__(self)
        self.board_id = board_id
        self.store = store
        self.feed = StickyFeed(board_id, store)
        self.putChild('events', self.feed)


    def render_GET(self, request):
        return render('chalkboard.html')


    def render_POST(self, request):
        action = request.args.get('action')[0]
        if action == 'add':
            return self.do_add(request)
        elif action == 'update':
            return self.do_update(request)
        elif action == 'remove':
            return self.do_remove(request)


    def do_add(self, request):
        note = request.args.get('note')[0]
        x = int(request.args.get('x')[0])
        y = int(request.args.get('y')[0])
        self.store.addSticky(self.board_id, note, x, y).addCallback(jsonfinish, request)
        return NOT_DONE_YET


    def do_update(self, request):
        sticky_id = int(request.args.get('id')[0])
        note = request.args.get('note')[0]
        x = int(request.args.get('x')[0])
        y = int(request.args.get('y')[0])
        self.store.updateSticky(self.board_id, sticky_id, note, x, y)
        return ''


    def do_remove(self, request):
        sticky_id = int(request.args.get('id')[0])
        self.store.removeSticky(self.board_id, sticky_id)
        return ''



class StickyFeed(Resource):

    
    def __init__(self, board_id, store):
        Resource.__init__(self)
        self.board_id = board_id
        self.store = store


    def render_GET(self, request):
        request.setHeader('Content-type', 'text/event-stream')
        request.write(sseMsg('hello', 'keep alive'))
        self.store.getStickies(self.board_id).addCallback(self.gotStickies, request)
        return NOT_DONE_YET


    def gotStickies(self, stickies, request):
        for sticky in stickies:
            self.eventReceived(request, 'add', sticky)
        self.store.subscribe(self.board_id, partial(self.eventReceived, request))


    def eventReceived(self, request, event, data):
        request.write(sseMsg(event, data))



def sseMsg(name, data):
    output = 'event: %s\n' % (name,)
    output += 'data: %s\n\n' % (json.dumps(data),)
    return output    



if __name__ == '__main__':
    from twisted.internet import reactor
    from twisted.web.server import Site
    fh = open('twistd.log', 'wb')
    import sys
    log.startLogging(sys.stdout)
    log.startLogging(fh)

    #store = EventStoreWrapper(InMemoryBoardStore())
    _store = DatabaseBoardStore('?')
    getReadySqlite('/tmp/d.sqlite').addCallback(_store.attachPool)
    store = EventStoreWrapper(_store)

    realm = ChalkboardRealm(store, {
        'john': ['john', 'things'],
        'sam': ['things'],
    })
    portal = Portal(realm, [FilePasswordDB('httpd.password')])
    credentialFactory = DigestCredentialFactory("md5", "localhost:9099")
    resource = HTTPAuthSessionWrapper(portal, [credentialFactory])

    root = Resource()
    root.putChild('', Redirect('/boards'))
    root.putChild('boards', resource)
    root.putChild('js', File('js'))
    site = Site(root)
    reactor.listenTCP(9099, site)
    reactor.run()



