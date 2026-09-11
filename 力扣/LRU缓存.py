class LRUCache:
    class dlinknode():
        def __init__(self,key=0,value=0):
            self.key=key
            self.value=value
            self.prev=None
            self.next=None

    def __init__(self, capacity: int):
        self.capacity=capacity
        self.map={}
        self.size=0
        self.head=self.dlinknode()
        self.tail=self.dlinknode()
        self.head.next=self.tail
        self.tail.prev=self.head


    def get(self, key: int):
        if key not in self.map:
            return -1
        else:
            node=self.map[key]
            self.delnode(node)
            self.addend(node)
            return node.value

    def put(self, key: int, value: int):
        node=self.dlinknode(key,value)
        if key in self.map:
            self.delnode(self.map[key])
            self.addend(node)
            self.map[key]=node
        else:
            self.size+=1
            if self.size>self.capacity:
                self.map.pop(self.head.next.key,None)
                self.delnode(self.head.next)
                self.size-=1
            self.map[key]=node
            self.addend(node)

    def addend(self,node):
        node.prev=self.tail.prev
        node.next=self.tail
        self.tail.prev.next=node
        self.tail.prev=node

    def delnode(self,node):
        node.prev.next=node.next
        node.next.prev=node.prev


# Your LRUCache object will be instantiated and called as such:
# obj = LRUCache(capacity)
# param_1 = obj.get(key)
# obj.put(key,value)