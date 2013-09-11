__all__ = ['ClassProperty']

class ClassProperty(object):
    '''
    Borrowed from: https://gist.github.com/josiahcarlson/1561563
    '''
    def __init__(self, get, set=None, delete=None):
        self.get = get
        self.set = set
        self.delete = delete

    def __get__(self, obj, cls=None):
        if cls is None:
            cls = type(obj)
        return self.get(cls)

    def __set__(self, obj, value):
        cls = type(obj)
        self.set(cls, value)

    def __delete__(self, obj):
        cls = type(obj)
        self.delete(cls)

    def getter(self, get):
        return ClassProperty(get, self.set, self.delete)

    def setter(self, set):
        return ClassProperty(self.get, set, self.delete)

    def deleter(self, delete):
        return ClassProperty(self.get, self.set, delete)
