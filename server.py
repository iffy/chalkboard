from twisted.web.resource import Resource
from twisted.web.server import NOT_DONE_YET
from collections import defaultdict
from functools import partial
from jinja2 import Environment, FileSystemLoader
import json

jenv = Environment(loader=FileSystemLoader('.'))

def render(name, params=None):
    template = jenv.get_template(name)
    return template.render(params or {}).encode('utf-8')



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


    def addSticky(self, board_id, text, x, y):
        id = self.nextId()
        self.boards[board_id].append({
            'id': id,
            'text': text,
            'x': x,
            'y': y,
        })
        ret = self.boards[board_id][-1]
        self.emit(board_id, 'add', ret)
        return ret


    def removeSticky(self, board_id, sticky_id):
        found = [x for x in self.boards[board_id] if x['id'] == sticky_id]
        for f in found:
            self.boards[board_id].remove(f)
            self.emit(board_id, 'remove', f)


    def updateSticky(self, board_id, sticky_id, text, x, y):
        found = [s for s in self.boards[board_id] if s['id'] == sticky_id]
        for f in found:
            f.update({
                'text': text,
                'x': x,
                'y': y,
            })
            self.boards[board_id].remove(f)
            self.boards[board_id].append(f)
            self.emit(board_id, 'update', f)

    
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



store = InMemoryBoardStore()


class Chalkboard(Resource):


    def __init__(self, board_id):
        Resource.__init__(self)
        self.board_id = board_id
        self.feed = StickyFeed(board_id)
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
        text = request.args.get('text')[0]
        x = int(request.args.get('x')[0])
        y = int(request.args.get('y')[0])
        s = store.addSticky(self.board_id, text, x, y)
        return json.dumps(s).encode('utf-8')


    def do_update(self, request):
        sticky_id = int(request.args.get('id')[0])
        text = request.args.get('text')[0]
        x = int(request.args.get('x')[0])
        y = int(request.args.get('y')[0])
        store.updateSticky(self.board_id, sticky_id, text, x, y)
        return ''


    def do_remove(self, request):
        sticky_id = int(request.args.get('id')[0])
        store.removeSticky(self.board_id, sticky_id)
        return ''



class StickyFeed(Resource):

    
    def __init__(self, board_id):
        Resource.__init__(self)
        self.board_id = board_id


    def render_GET(self, request):
        request.setHeader('Content-type', 'text/event-stream')
        request.write(sseMsg('hello', 'keep alive'))
        stickies = store.getStickies(self.board_id)
        for sticky in stickies:
            self.eventReceived(request, 'add', sticky)
        store.subscribe(self.board_id, partial(self.eventReceived, request))
        return NOT_DONE_YET


    def eventReceived(self, request, event, data):
        request.write(sseMsg(event, data))



def sseMsg(name, data):
    output = 'event: %s\n' % (name,)
    output += 'data: %s\n\n' % (json.dumps(data),)
    return output    



if __name__ == '__main__':
    from twisted.internet import reactor
    from twisted.web.static import File
    from twisted.web.server import Site
    from twisted.python import log
    fh = open('twistd.log', 'wb')
    log.startLogging(fh)
    root = Resource()
    root.putChild('', Chalkboard(1))
    root.putChild('js', File('js'))
    site = Site(root)
    reactor.listenTCP(9099, site)
    reactor.run()



